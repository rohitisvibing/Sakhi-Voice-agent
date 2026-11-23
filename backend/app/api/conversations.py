from fastapi import APIRouter

from app.api.ownership import owned_conversation, owned_mission
from app.api.profiles import Principal
from app.core.auth import check_daily_limit
from app.core.deps import get_container
from app.core.errors import AppError, ErrorKind
from app.models.common import Schema
from app.models.conversation import (
    Approval,
    CallStatus,
    Conversation,
    ConversationMode,
    ConversationState,
    CreateConversation,
)
from app.models.mission import Mission, MissionStatus
from app.models.report import OutcomeReport
from app.models.transcript import TranscriptEvent
from app.realtime.session_manager import manager
from app.services import billing

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationDetail(Schema):
    conversation: Conversation
    mission: Mission
    events: list[TranscriptEvent]
    approvals: list[Approval]


@router.post("", response_model=Conversation)
async def create(body: CreateConversation, pid: Principal) -> Conversation:
    c = get_container()
    m = await owned_mission(c, body.mission_id, pid)
    if m.status not in (MissionStatus.prepared, MissionStatus.active, MissionStatus.completed):
        raise AppError(ErrorKind.invalid_state, "Prepare the agent before starting.")
    phone = body.mode == ConversationMode.phone
    if phone:
        if not (c.settings.phone_available and c.telephony is not None):
            raise AppError(ErrorKind.missing_key, "Phone calls aren't set up on this server yet.")
        if not c.voice_available:
            raise AppError(ErrorKind.missing_key, "Phone calls need speech (a Sarvam key).")
        if body.recipient is None:
            raise AppError(ErrorKind.invalid_state, "Choose who Sakhi should call.")
    await check_daily_limit(c, pid, "conversations")
    await billing.check_can_start_call(c, m)
    conv = Conversation(mission_id=m.id, mode=body.mode, state=ConversationState.ready,
                        voice_enabled=phone or (body.voice_enabled and c.voice_available))
    if phone and body.recipient:
        conv.recipient_name, conv.recipient_phone = body.recipient.name, body.recipient.phone
        conv.call_status = CallStatus.preparing
    return await c.repo.save_conversation(conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def detail(conversation_id: str, pid: Principal) -> ConversationDetail:
    c = get_container()
    live = manager.peek(conversation_id)
    conv, mission = await owned_conversation(c, conversation_id, pid,
                                             live.conv if live else None)
    events = live.events if live else await c.repo.list_events(conversation_id)
    approvals = list(live.approvals.values()) if live else await c.repo.list_approvals(
        conversation_id)
    return ConversationDetail(conversation=conv, mission=mission, events=events,
                              approvals=approvals)


@router.get("/{conversation_id}/report", response_model=OutcomeReport)
async def report(conversation_id: str, pid: Principal) -> OutcomeReport:
    c = get_container()
    await owned_conversation(c, conversation_id, pid)
    r = await c.repo.get_report(conversation_id)
    if r:
        return r
    live = manager.peek(conversation_id)
    if live and live.report_task and not live.report_task.done():
        raise AppError(ErrorKind.invalid_state, "The report is still being written.",
                       retryable=True)
    raise AppError(ErrorKind.not_found, "No report yet. End the conversation to create one.")
