"""Phone calls: who you've called, placing a call after you confirm, and Exotel's callbacks."""

import logging
from typing import Literal

from fastapi import APIRouter, Request

from app.api.ownership import owned_conversation
from app.api.profiles import Principal
from app.core import telephony_tokens as tokens
from app.core.deps import Container, get_container
from app.core.errors import AppError, ErrorKind
from app.models.common import Schema
from app.models.conversation import (
    CALL_ACTIVE,
    CALL_ENDED,
    CallStatus,
    Conversation,
    ConversationMode,
    ConversationState,
    Recipient,
)
from app.models.transcript import EventKind, Speaker, TranscriptEvent
from app.realtime.session_manager import manager
from app.services import billing

log = logging.getLogger(__name__)
router = APIRouter(tags=["phone"])

STREAM_TOKEN_TTL = 10 * 60
STATUS_TOKEN_TTL = 2 * 60 * 60
# Exotel's words -> ours. "in-progress" before the stream connects means "answered".
EXOTEL_STATUS = {
    "ringing": CallStatus.ringing,
    "answered": CallStatus.connected,
    "in-progress": CallStatus.connected,
    "completed": CallStatus.completed,
    "failed": CallStatus.failed,
    "busy": CallStatus.busy,
    "no-answer": CallStatus.no_answer,
    "canceled": CallStatus.failed,
}
_RANK = {CallStatus.preparing: 0, CallStatus.initiating: 1, CallStatus.ringing: 2,
         CallStatus.connected: 3, CallStatus.in_conversation: 4, CallStatus.navigating_ivr: 4}
_NOT_REACHED = {
    CallStatus.busy: "The line was busy.",
    CallStatus.no_answer: "{name} didn't answer.",
    CallStatus.failed: "The call couldn't be connected.",
}


class DialRequest(Schema):
    confirm: Literal[True]


@router.get("/api/recipients/recent", response_model=list[Recipient])
async def recent(pid: Principal) -> list[Recipient]:
    return await get_container().repo.recent_recipients(pid)


def _urls(c: Container, conversation_id: str) -> tuple[str, str]:
    s = c.settings
    base = s.public_base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    stream = tokens.make(s.telephony_secret, conversation_id, tokens.STREAM, STREAM_TOKEN_TTL)
    status = tokens.make(s.telephony_secret, conversation_id, tokens.STATUS, STATUS_TOKEN_TTL)
    return (f"{ws_base}/ws/telephony/exotel?sample-rate=16000&t={stream}",
            f"{base}/api/telephony/exotel/status?t={status}")


@router.post("/api/conversations/{conversation_id}/dial", response_model=Conversation)
async def dial(conversation_id: str, body: DialRequest, pid: Principal) -> Conversation:
    """Place the call. Only after the person confirmed who is being called (`confirm: true`)."""
    c = get_container()
    conv, mission = await owned_conversation(c, conversation_id, pid)
    if conv.mode != ConversationMode.phone or not conv.recipient_phone:
        raise AppError(ErrorKind.invalid_state, "This conversation isn't a phone call.")
    if not (c.settings.phone_available and c.telephony is not None):
        raise AppError(ErrorKind.missing_key, "Phone calls aren't set up on this server yet.")
    if conv.state in (ConversationState.completed, ConversationState.failed) and conv.call_sid \
            and conv.call_status not in (CallStatus.busy, CallStatus.no_answer, CallStatus.failed):
        raise AppError(ErrorKind.invalid_state, "This call has already happened.")
    if conv.call_status in CALL_ACTIVE:
        raise AppError(ErrorKind.invalid_state, "A call is already in progress.")
    await billing.check_can_start_call(c, mission)
    stream_url, status_url = _urls(c, conv.id)
    s = c.settings
    call = await c.telephony.connect_call(
        to=conv.recipient_phone, stream_url=stream_url, status_url=status_url,
        time_limit=s.phone_call_limit_seconds + 30, timeout=s.phone_ring_timeout_s,
        custom_field=conv.id)
    conv.call_sid = call.get("Sid")
    conv.call_status = CallStatus.initiating
    conv.state = ConversationState.ready
    conv.ended_at = None
    await c.repo.save_conversation(conv)
    await c.repo.add_event(TranscriptEvent(
        conversation_id=conv.id, speaker=Speaker.system, kind=EventKind.event,
        text=f"Calling {conv.recipient_name}…", meta={"call_status": "initiating"}))
    return conv


async def _payload(req: Request) -> dict:
    if "json" in (req.headers.get("content-type") or ""):
        try:
            data = await req.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    form = await req.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


@router.post("/api/telephony/exotel/status")
async def exotel_status(req: Request, t: str | None = None) -> dict:
    """Exotel's status callback. Unsigned by Exotel, so the URL carries our signed token."""
    c = get_container()
    conversation_id = tokens.check(c.settings.telephony_secret, t, tokens.STATUS)
    if not conversation_id:
        raise AppError(ErrorKind.unauthorized, "Invalid callback token.")
    data = await _payload(req)
    raw = str(data.get("Status") or data.get("CallStatus") or data.get("EventType") or "").lower()
    status = EXOTEL_STATUS.get(raw)
    if status is None:
        return {"ok": True}
    await apply_status(c, conversation_id, status, call_sid=data.get("CallSid"))
    return {"ok": True}


async def apply_status(c: Container, conversation_id: str, status: CallStatus,
                       call_sid: str | None = None) -> None:
    """Move a call forward (never backward); end calls that never connected."""
    live = manager.peek(conversation_id)
    conv = live.conv if live else await c.repo.get_conversation(conversation_id)
    if conv is None or (call_sid and conv.call_sid and call_sid != conv.call_sid):
        return  # an older attempt's callback
    current = conv.call_status
    if current in CALL_ENDED:
        return
    if status not in CALL_ENDED and _RANK.get(status, 0) <= _RANK.get(current, 0):
        return  # duplicate or late
    if live:
        if status == CallStatus.completed:
            if live.state not in (ConversationState.completed, ConversationState.failed):
                await live.finish("The call ended.")
            return
        await live.set_call_status(status)
        return
    conv.call_status = status
    note = None
    if status in _NOT_REACHED and conv.state == ConversationState.ready:
        note = _NOT_REACHED[status].format(name=conv.recipient_name or "They")
    elif status == CallStatus.ringing:
        note = "Ringing…"
    await c.repo.save_conversation(conv)
    if note:
        await c.repo.add_event(TranscriptEvent(
            conversation_id=conv.id, speaker=Speaker.system, kind=EventKind.event, text=note,
            meta={"call_status": status.value}))
