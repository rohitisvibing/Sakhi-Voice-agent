"""WebSocket endpoint: one socket per client per conversation.

Client -> server
  first frame, when sign-in is on: {"type": "auth", "token": <Supabase access token>}
    (no or bad token: close 4401; someone else's conversation: close 4404)
  binary frames: mono PCM16 @16 kHz microphone audio (live mode)
  JSON: {"type": "start"|"pause"|"resume"|"interrupt"|"end"|"mute"|"unmute"|"takeover"
                 |"release"|"ping"}
        {"type": "intervene", "text", "urgent"?}  principal nudge (urgent cuts in now)
        {"type": "say", "text"}                  takeover: relay text in the agent's voice
        {"type": "counterparty_text", "text"}    live mode: type what the other party said
        {"type": "approval", "id", "decision", "alternative_text"}
        {"type": "playback_done", "turn_id"}
Server -> client
  snapshot, state, transcript.partial, transcript.final, transcript.update (meta patch),
  instruction.status {id, status: queued|applied|noted, applied_in}, audio, audio.end, audio.stop,
  negotiation_state, approval.request, approval.resolved, report.pending, report.ready,
  error, pong
"""

import asyncio
import contextlib
import json
import logging
from datetime import timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.ownership import owned_conversation
from app.core.auth import auth_required, authenticate
from app.core.deps import get_container
from app.core.errors import AppError
from app.models.conversation import (
    ApprovalDecision,
    ApprovalStatus,
    ConversationMode,
    ConversationState,
)
from app.models.principal import PrincipalProfile

from .session_manager import manager

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_socket(ws: WebSocket, conversation_id: str) -> None:
    await ws.accept()
    c = get_container()
    try:
        pid = await authenticate(c, await _auth_frame(ws) if auth_required(c.settings) else None)
    except (AppError, TimeoutError, ValueError, WebSocketDisconnect) as e:
        if isinstance(e, AppError):
            await _try_send(ws, {"type": "error", "error": e.to_dict() | {"fatal": True}})
        await _try_close(ws, 4401)
        return
    try:
        live = manager.peek(conversation_id)
        conv, _ = await owned_conversation(c, conversation_id, pid, live.conv if live else None)
        if conv.mode == ConversationMode.phone and live is None:
            # The call runs where Exotel's stream landed (maybe another server): follow it.
            await _follow(ws, c, conversation_id)
            return
        session = await manager.get(c, conversation_id)
    except AppError as e:
        await ws.send_json({"type": "error", "error": e.to_dict() | {"fatal": True}})
        await ws.close(code=4404)
        return

    send_lock = asyncio.Lock()

    async def sink(msg: dict) -> None:
        async with send_lock:
            await ws.send_json(msg)

    session.attach(sink)
    try:
        await sink(session.snapshot())  # a client that already left raises here
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                await session.on_audio(data)
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            await _dispatch(session, payload, sink)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket error")
    finally:
        session.detach(sink)


async def _auth_frame(ws: WebSocket) -> str | None:
    """Browsers can't set headers on a WebSocket, so the token comes as the first frame."""
    async with asyncio.timeout(10):
        first = json.loads(await ws.receive_text())
    if not isinstance(first, dict) or first.get("type") != "auth":
        return None
    token = first.get("token")
    return token if isinstance(token, str) else None


async def _try_send(ws: WebSocket, msg: dict) -> None:
    with contextlib.suppress(Exception):  # the client may already be gone
        await ws.send_json(msg)


async def _try_close(ws: WebSocket, code: int) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code)


COMMANDS = {"pause", "resume", "interrupt", "end", "takeover", "release", "intervene", "say",
            "approval"}


async def _dispatch(session, p: dict, sink) -> None:
    t = p.get("type")
    match t:
        case "auth":
            pass  # sign-in is checked once, at connect; a late or repeated frame is harmless
        case "ping":
            await sink({"type": "pong"})
        case "start":
            if not session.phone:  # a phone call starts when the callee answers
                await session.start()
        case "mute" | "unmute" | "counterparty_text" | "playback_done":
            await _local_only(session, p)
        case _ if t in COMMANDS:
            err = await dispatch_command(session, t, p)
            if err:
                await sink({"type": "error", "error": err})
        case _:
            await sink({"type": "error", "error": {"kind": "invalid_state",
                                                   "message": f"Unknown message type: {t}"}})


async def _local_only(session, p: dict) -> None:
    match p.get("type"):
        case "mute":
            await session.set_muted(True)
        case "unmute":
            await session.set_muted(False)
        case "counterparty_text":
            await session.counterparty_said(str(p.get("text", ""))[:2000], p.get("language"),
                                            "text")
        case "playback_done":
            session.playback_done(str(p.get("turn_id")))


async def dispatch_command(session, t: str | None, p: dict) -> dict | None:
    """The principal's controls, from a local browser socket or relayed through the database."""
    match t:
        case "pause":
            await session.pause()
        case "resume":
            await session.resume()
        case "interrupt":
            await session.interrupt()
        case "end":
            await session.finish("You ended the call." if session.phone
                                 else "Session ended by you.")
        case "takeover":
            await session.take_over()
        case "release":
            await session.release()
        case "intervene":
            await session.intervene(str(p.get("text", ""))[:2000], urgent=bool(p.get("urgent")))
        case "say":
            await session.principal_say(str(p.get("text", ""))[:2000])
        case "approval":
            try:
                decision = ApprovalDecision(decision=p.get("decision"),
                                            alternative_text=p.get("alternative_text"))
            except ValidationError:
                return {"kind": "invalid_state", "message": "Invalid approval decision."}
            await session.decide(str(p.get("id")), decision)
    return None


# ---- following a phone call that runs on another server -------------------------------------
FOLLOW_POLL_S = 0.75


async def _follow(ws: WebSocket, c, conversation_id: str) -> None:
    """Mirror the call from the database, in the same messages a local session sends, and
    queue the principal's controls for the server holding the call."""
    from .session import LiveSession

    repo = c.repo
    conv = await repo.get_conversation(conversation_id)
    mission = await repo.get_mission(conv.mission_id) if conv else None
    if conv is None or mission is None:
        await _try_close(ws, 4404)
        return
    profile = await repo.get_profile(mission.principal_id) \
        or PrincipalProfile.blank(mission.principal_id)
    events = await repo.list_events(conversation_id)
    approvals = await repo.list_approvals(conversation_id)
    view = LiveSession(c, conv, mission, profile, events, approvals)  # read-only, never started
    send_lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with send_lock:
            await ws.send_json(msg)

    await send(view.snapshot())
    seen = {e.id: dict(e.meta) for e in events}
    last_ts = events[-1].ts if events else None
    approval_status = {a.id: a.status for a in approvals}
    last = (conv.state, conv.call_status, conv.negotiation.model_dump_json())
    report_sent = False

    async def receive() -> None:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            text = msg.get("text")
            if not text:
                continue
            try:
                p = json.loads(text)
            except ValueError:
                continue
            t = p.get("type")
            if t == "ping":
                await send({"type": "pong"})
            elif t in COMMANDS:
                local = manager.peek(conversation_id)
                if local is not None:
                    await dispatch_command(local, t, p)
                else:
                    await repo.add_call_command(conversation_id, t, p)

    async def poll() -> None:
        nonlocal last_ts, last, report_sent
        while True:
            await asyncio.sleep(FOLLOW_POLL_S)
            since = last_ts - timedelta(seconds=20) if last_ts else None
            for e in await repo.list_events_since(conversation_id, since):
                if e.id not in seen:
                    seen[e.id] = dict(e.meta)
                    await send({"type": "transcript.final", "event": e.model_dump(mode="json")})
                elif e.meta != seen[e.id]:
                    seen[e.id] = dict(e.meta)
                    await send({"type": "transcript.update", "id": e.id, "meta": e.meta})
                if last_ts is None or e.ts > last_ts:
                    last_ts = e.ts
            fresh = await repo.get_conversation(conversation_id)
            if fresh is None:
                continue
            now = (fresh.state, fresh.call_status, fresh.negotiation.model_dump_json())
            if now != last:
                view.conv, view.state = fresh, fresh.state
                view.paused = fresh.state == ConversationState.paused
                if now[2] != last[2]:
                    await send({"type": "negotiation_state",
                                "negotiation": fresh.negotiation.model_dump(mode="json")})
                await send(view.state_msg())
                last = now
            for a in await repo.list_approvals(conversation_id):
                before = approval_status.get(a.id)
                if before == a.status:
                    continue
                approval_status[a.id] = a.status
                kind = "approval.request" if a.status == ApprovalStatus.pending \
                    else "approval.resolved"
                await send({"type": kind, "approval": a.model_dump(mode="json")})
            if fresh.state in (ConversationState.completed, ConversationState.failed) \
                    and not report_sent:
                report = await repo.get_report(conversation_id)
                if report:
                    report_sent = True
                    await send({"type": "report.ready",
                                "report": report.model_dump(mode="json")})

    tasks = [asyncio.create_task(receive()), asyncio.create_task(poll())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            if t.done() and not t.cancelled() and t.exception():
                log.warning("follower stopped: %s", t.exception())
    finally:
        for t in tasks:
            t.cancel()
