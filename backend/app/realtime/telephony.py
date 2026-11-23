"""Exotel's media stream: the phone call's audio, both ways, for one conversation.

Exotel opens this socket when the callee answers (StreamUrl from the dial request) and sends
JSON events: connected, start, media, dtmf, mark, stop. The session for the conversation lives
in this process for the whole call. The principal's browser may be connected to a different
server, so its controls arrive through the call_commands table (see `_relay_commands`).
"""

import asyncio
import base64
import binascii
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import telephony_tokens as tokens
from app.core.deps import Container, get_container
from app.core.errors import AppError
from app.models.conversation import CallStatus, ConversationMode, ConversationState

from .phone import PhoneChannel, resample
from .session import LiveSession
from .session_manager import manager

log = logging.getLogger(__name__)
router = APIRouter()
COMMAND_POLL_S = 0.5


@router.websocket("/ws/telephony/exotel")
async def exotel_stream(ws: WebSocket) -> None:
    await ws.accept()
    c = get_container()
    session: LiveSession | None = None
    channel: PhoneChannel | None = None
    relay: asyncio.Task | None = None
    in_rate = 16000
    hung_up_by_them = False
    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            event = msg.get("event")
            if event == "start" and session is None:
                start = msg.get("start") or {}
                params = start.get("custom_parameters") or {}
                token = ws.query_params.get("t") or params.get("t")
                conv_id = tokens.check(c.settings.telephony_secret, token, tokens.STREAM,
                                       single_use=True)
                if not conv_id:
                    log.warning("exotel stream with a bad token; closing")
                    break
                fmt = start.get("media_format") or {}
                in_rate = int(fmt.get("sample_rate") or ws.query_params.get("sample-rate")
                              or 16000)
                session, channel = await _begin(c, conv_id, ws, msg.get("stream_sid")
                                                or start.get("stream_sid"), in_rate)
                if session is None:
                    break
                relay = asyncio.create_task(_relay_commands(c, session))
            elif event == "media" and session is not None:
                payload = (msg.get("media") or {}).get("payload")
                if not payload:
                    continue
                try:
                    pcm = base64.b64decode(payload)
                except (binascii.Error, ValueError):
                    continue
                pcm = resample(pcm, in_rate, PhoneChannel.STT_RATE)
                if channel is not None:
                    channel.on_inbound(pcm)
                await session.on_audio(pcm)
            elif event == "mark" and channel is not None:
                channel.on_mark(str((msg.get("mark") or {}).get("name")))
            elif event == "dtmf" and session is not None:
                digit = (msg.get("dtmf") or {}).get("digit")
                if digit:
                    await session.system(f"They pressed {digit}")
            elif event == "stop":
                reason = (msg.get("stop") or {}).get("reason")
                hung_up_by_them = reason in (None, "callended")
                break
    except Exception:
        log.exception("exotel stream error")
    finally:
        if relay:
            relay.cancel()
        if session is not None:
            if channel is not None:
                channel.closed = True  # the stream is gone; nothing more can be sent
            if session.state not in (ConversationState.completed, ConversationState.failed):
                who = session.conv.recipient_name or "They"
                await session.finish(f"{who} hung up." if hung_up_by_them else "The call ended.")
            if session.report_task:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(session.report_task), timeout=45)
            manager.sessions.pop(session.conv.id, None)
        with contextlib.suppress(Exception):
            await ws.close()


async def _begin(c: Container, conv_id: str, ws: WebSocket, stream_sid: str | None,
                 rate: int) -> tuple[LiveSession | None, PhoneChannel | None]:
    try:
        session = await manager.get(c, conv_id)
    except AppError as e:
        log.warning("exotel stream for unknown conversation %s: %s", conv_id, e.message)
        return None, None
    if session.conv.mode != ConversationMode.phone or not isinstance(session.channel,
                                                                     PhoneChannel):
        return None, None
    channel = session.channel
    channel.bind(ws, stream_sid, rate)
    await session.start_phone()
    return session, channel


async def _relay_commands(c: Container, session: LiveSession) -> None:
    """Controls the principal sent from a browser connected to another server."""
    from .websocket_manager import dispatch_command  # circular at import time

    while session.state not in (ConversationState.completed, ConversationState.failed):
        await asyncio.sleep(COMMAND_POLL_S)
        try:
            commands = await c.repo.take_call_commands(session.conv.id)
        except Exception as e:  # noqa: BLE001 - keep the call going
            log.warning("reading call commands failed: %s", e)
            continue
        for cmd in commands:
            with contextlib.suppress(Exception):
                await dispatch_command(session, cmd.get("type"), cmd.get("payload") or {})


__all__ = ["CallStatus", "router"]
