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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.ownership import owned_conversation
from app.core.auth import auth_required, authenticate
from app.core.deps import get_container
from app.core.errors import AppError
from app.models.conversation import ApprovalDecision

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
        await owned_conversation(c, conversation_id, pid, live.conv if live else None)
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


async def _dispatch(session, p: dict, sink) -> None:
    t = p.get("type")
    match t:
        case "auth":
            pass  # sign-in is checked once, at connect; a late or repeated frame is harmless
        case "ping":
            await sink({"type": "pong"})
        case "start":
            await session.start()
        case "pause":
            await session.pause()
        case "resume":
            await session.resume()
        case "interrupt":
            await session.interrupt()
        case "end":
            await session.finish("Session ended by you.")
        case "mute":
            await session.set_muted(True)
        case "unmute":
            await session.set_muted(False)
        case "takeover":
            await session.take_over()
        case "release":
            await session.release()
        case "intervene":
            await session.intervene(str(p.get("text", ""))[:2000], urgent=bool(p.get("urgent")))
        case "say":
            await session.principal_say(str(p.get("text", ""))[:2000])
        case "counterparty_text":
            await session.counterparty_said(str(p.get("text", ""))[:2000], p.get("language"),
                                            "text")
        case "approval":
            try:
                decision = ApprovalDecision(decision=p.get("decision"),
                                            alternative_text=p.get("alternative_text"))
            except ValidationError:
                await sink({"type": "error", "error": {"kind": "invalid_state",
                                                       "message": "Invalid approval decision."}})
                return
            await session.decide(str(p.get("id")), decision)
        case "playback_done":
            session.playback_done(str(p.get("turn_id")))
        case _:
            await sink({"type": "error", "error": {"kind": "invalid_state",
                                                   "message": f"Unknown message type: {t}"}})
