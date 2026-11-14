"""Persistence interface. Services depend on this, not on Supabase directly."""

from abc import ABC, abstractmethod
from datetime import datetime

from app.models.billing import Subscription
from app.models.conversation import Approval, Conversation
from app.models.mission import AgentTemplate, Mission
from app.models.principal import PrincipalProfile
from app.models.report import OutcomeReport
from app.models.transcript import TranscriptEvent


class Repository(ABC):
    name = "repo"

    @abstractmethod
    async def get_profile(self, principal_id: str) -> PrincipalProfile | None: ...
    @abstractmethod
    async def save_profile(self, profile: PrincipalProfile) -> PrincipalProfile: ...

    @abstractmethod
    async def list_templates(self) -> list[AgentTemplate]: ...

    @abstractmethod
    async def save_mission(self, mission: Mission) -> Mission: ...
    @abstractmethod
    async def get_mission(self, mission_id: str) -> Mission | None: ...
    @abstractmethod
    async def list_missions(self, principal_id: str) -> list[Mission]: ...
    @abstractmethod
    async def delete_mission(self, mission_id: str) -> None: ...
    @abstractmethod
    async def count_created_today(self, principal_id: str, kind: str) -> int:
        """Missions ("missions") or conversations ("conversations") this principal created
        since midnight UTC."""

    @abstractmethod
    async def save_conversation(self, conv: Conversation) -> Conversation: ...
    @abstractmethod
    async def get_conversation(self, conversation_id: str) -> Conversation | None: ...
    @abstractmethod
    async def list_conversations(self, mission_ids: list[str]) -> list[Conversation]: ...

    @abstractmethod
    async def add_event(self, event: TranscriptEvent) -> None: ...
    @abstractmethod
    async def list_events(self, conversation_id: str) -> list[TranscriptEvent]: ...
    @abstractmethod
    async def update_event_meta(self, event_id: str, meta: dict) -> None: ...

    @abstractmethod
    async def save_approval(self, approval: Approval) -> None: ...
    @abstractmethod
    async def list_approvals(self, conversation_id: str) -> list[Approval]: ...

    @abstractmethod
    async def save_report(self, report: OutcomeReport) -> None: ...
    @abstractmethod
    async def get_report(self, conversation_id: str) -> OutcomeReport | None: ...

    # billing -------------------------------------------------------------------------
    @abstractmethod
    async def get_trial_mission(self, principal_id: str) -> str | None: ...
    @abstractmethod
    async def set_trial_mission(self, principal_id: str, mission_id: str) -> None: ...
    @abstractmethod
    async def get_subscription(self, principal_id: str) -> Subscription | None: ...
    @abstractmethod
    async def find_subscription(self, razorpay_subscription_id: str) -> Subscription | None: ...
    @abstractmethod
    async def save_subscription(self, sub: Subscription) -> None: ...
    @abstractmethod
    async def talk_seconds(self, principal_id: str, *, since: datetime | None = None,
                           mission_id: str | None = None) -> int:
        """Talk time across the principal's conversations created since `since`, or on one
        mission."""
