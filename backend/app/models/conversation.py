"""Conversation state machine and the structured negotiation state the engine maintains."""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from .common import Schema, new_id, utcnow
from .mission import ApprovalKind


class ConversationState(StrEnum):
    idle = "idle"
    preparing = "preparing"
    ready = "ready"
    connecting = "connecting"
    listening = "listening"
    thinking = "thinking"
    speaking = "speaking"
    interrupted = "interrupted"
    paused = "paused"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    failed = "failed"


S = ConversationState
_LIVE = {S.listening, S.thinking, S.speaking, S.interrupted}

TRANSITIONS: dict[ConversationState, set[ConversationState]] = {
    S.idle: {S.preparing, S.ready, S.failed},
    S.preparing: {S.ready, S.failed},
    S.ready: {S.connecting, S.failed, S.completed},
    S.connecting: {S.speaking, S.thinking, S.listening, S.failed, S.completed},
    S.listening: {S.thinking, S.speaking, S.interrupted, S.paused, S.awaiting_approval,
                  S.completed, S.failed},
    S.thinking: {S.speaking, S.listening, S.paused, S.awaiting_approval, S.completed, S.failed},
    S.speaking: {S.listening, S.thinking, S.interrupted, S.paused, S.awaiting_approval,
                 S.completed, S.failed},
    S.interrupted: {S.listening, S.thinking, S.paused, S.completed, S.failed},
    S.paused: {S.listening, S.thinking, S.speaking, S.awaiting_approval, S.completed, S.failed},
    S.awaiting_approval: {S.thinking, S.speaking, S.listening, S.paused, S.completed, S.failed},
    S.completed: set(),
    S.failed: {S.connecting},
}


class InvalidTransition(Exception):
    pass


def can_transition(src: ConversationState, dst: ConversationState) -> bool:
    return src == dst or dst in TRANSITIONS[src]


def is_live(state: ConversationState) -> bool:
    return state in _LIVE


class ConversationMode(StrEnum):
    simulated = "simulated"   # LLM role-plays the counterparty
    live = "live"             # a real person speaks through the browser microphone
    phone = "phone"           # Sakhi places a phone call (Exotel) and talks to the callee


class CallStatus(StrEnum):
    """Where a phone call is, as the principal sees it."""

    preparing = "preparing"
    initiating = "initiating"
    ringing = "ringing"
    connected = "connected"
    in_conversation = "in_conversation"
    navigating_ivr = "navigating_ivr"
    completed = "completed"
    failed = "failed"
    busy = "busy"
    no_answer = "no_answer"


CALL_ACTIVE = {CallStatus.initiating, CallStatus.ringing, CallStatus.connected,
               CallStatus.in_conversation, CallStatus.navigating_ivr}
CALL_ENDED = {CallStatus.completed, CallStatus.failed, CallStatus.busy, CallStatus.no_answer}


def normalize_phone(raw: str, default_country: str = "91") -> str:
    """E.164 (+919876543210) from what people type: spaces, dashes, a leading 0 or 91."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+"):
        num = "+" + re.sub(r"\D", "", digits[1:])
    else:
        d = re.sub(r"\D", "", digits).lstrip("0")
        if len(d) == 10:
            num = f"+{default_country}{d}"
        elif len(d) == 12 and d.startswith(default_country):
            num = f"+{d}"
        else:
            num = f"+{d}"
    if not re.fullmatch(r"\+\d{8,15}", num):
        raise ValueError("Enter a full phone number, like 98765 43210.")
    if num.startswith("+91") and not re.fullmatch(r"\+91[6-9]\d{9}", num):
        raise ValueError("Indian mobile numbers have 10 digits and start with 6-9.")
    return num


class Recipient(Schema):
    name: str = Field(min_length=1, max_length=80)
    phone: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return v.strip()

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class Offer(Schema):
    by: str = Field(description="agent or counterparty")
    amount: float | None = None
    quantity_name: str | None = Field(default=None, description="Which target/limit it refers to")
    terms: str
    turn: int = 0


class NegotiationState(Schema):
    phase: str = "opening"
    established_facts: list[str] = Field(default_factory=list)
    counterparty_positions: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    offers_received: list[Offer] = Field(default_factory=list)
    offers_made: list[Offer] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    commitments: list[str] = Field(default_factory=list)
    remaining_objectives: list[str] = Field(default_factory=list)
    next_action: str | None = None
    approval_required: bool = False
    objective_met: bool = False
    ivr_path: list[str] = Field(default_factory=list,
                                description="Keys pressed in phone menus, e.g. '2: Billing'")
    summary: str = ""


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    alternative = "alternative"


class Approval(Schema):
    id: str = Field(default_factory=new_id)
    conversation_id: str
    kind: ApprovalKind
    request: str = Field(description="What the counterparty asked for / situation")
    proposed: str = Field(description="What the agent proposes to say or commit to")
    reason: str = ""
    disclosure_key: str | None = None
    status: ApprovalStatus = ApprovalStatus.pending
    alternative_text: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None
    # English renderings for the principal; not stored in the approvals table.
    request_en: str | None = None
    proposed_en: str | None = None


class ApprovalDecision(Schema):
    decision: ApprovalStatus
    alternative_text: str | None = None


class Conversation(Schema):
    id: str = Field(default_factory=new_id)
    mission_id: str
    mode: ConversationMode = ConversationMode.simulated
    state: ConversationState = ConversationState.ready
    negotiation: NegotiationState = Field(default_factory=NegotiationState)
    voice_enabled: bool = True
    talk_seconds: int = 0  # metered while the call is live (billing)
    # Phone mode only (Exotel): who is called, and where the call is.
    recipient_name: str | None = None
    recipient_phone: str | None = None
    call_sid: str | None = None
    call_status: CallStatus | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None


class CreateConversation(Schema):
    mission_id: str
    mode: ConversationMode = ConversationMode.simulated
    voice_enabled: bool = True
    recipient: Recipient | None = None  # required for phone mode
