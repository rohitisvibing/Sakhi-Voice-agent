"""Supabase (PostgreSQL) repository using the service-role key, server-side only."""

from supabase import AsyncClient, acreate_client

from app.core.config import Settings
from app.models.billing import Subscription
from app.models.common import utcnow
from app.models.conversation import Approval, Conversation
from app.models.mission import AgentTemplate, Mission
from app.models.principal import PrincipalProfile, ProfileField
from app.models.report import OutcomeReport
from app.models.transcript import TranscriptEvent

from .base import Repository


def _j(model) -> dict:
    return model.model_dump(mode="json")


class SupabaseRepository(Repository):
    name = "supabase"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncClient | None = None

    async def client(self) -> AsyncClient:
        if self._client is None:
            self._client = await acreate_client(self.settings.supabase_url,
                                                self.settings.supabase_service_role_key)
        return self._client

    async def _t(self, name: str):
        return (await self.client()).table(name)

    # profiles ------------------------------------------------------------------------
    async def get_profile(self, principal_id):
        res = await (await self._t("principals")).select("*").eq("id", principal_id) \
            .limit(1).execute()
        if not res.data:
            return None
        row = res.data[0]
        fields = await (await self._t("profile_fields")).select("*") \
            .eq("principal_id", principal_id).order("position").execute()
        return PrincipalProfile(
            id=row["id"], preferred_languages=row.get("preferred_languages") or [],
            communication_preferences=row.get("communication_preferences"),
            background=row.get("background"),
            fields=[ProfileField(id=f["id"], key=f["key"], label=f["label"], value=f["value"],
                                 disclosure=f["disclosure"], is_custom=f["is_custom"])
                    for f in fields.data])

    async def save_profile(self, profile):
        await (await self._t("principals")).upsert({
            "id": profile.id, "preferred_languages": profile.preferred_languages,
            "communication_preferences": profile.communication_preferences,
            "background": profile.background}).execute()
        keep = [f.id for f in profile.fields]
        q = (await self._t("profile_fields")).delete().eq("principal_id", profile.id)
        if keep:
            q = q.not_.in_("id", keep)
        await q.execute()
        if profile.fields:
            await (await self._t("profile_fields")).upsert([
                {"id": f.id, "principal_id": profile.id, "key": f.key, "label": f.label,
                 "value": f.value, "disclosure": f.disclosure.value, "is_custom": f.is_custom,
                 "position": i} for i, f in enumerate(profile.fields)]).execute()
        return profile

    async def list_templates(self):
        res = await (await self._t("agent_templates")).select("*").order("name").execute()
        return [AgentTemplate(**{k: r[k] for k in ("id", "name", "description", "agent_type",
                                                   "prompt_example")}) for r in res.data]

    # missions ------------------------------------------------------------------------
    @staticmethod
    def _mission_row(m: Mission) -> dict:
        d = _j(m)
        return {"id": d["id"], "principal_id": d["principal_id"], "status": d["status"],
                "raw_input": d["raw_input"], "agent_type": d["agent_type"],
                "title": m.spec.title, "spec": d["spec"], "extraction": d["extraction"],
                "brief": d["brief"], "template_id": d["template_id"],
                "created_at": d["created_at"], "updated_at": d["updated_at"]}

    async def save_mission(self, mission):
        await (await self._t("missions")).upsert(self._mission_row(mission)).execute()
        return mission

    async def get_mission(self, mission_id):
        res = await (await self._t("missions")).select("*").eq("id", mission_id).limit(1) \
            .execute()
        return Mission.model_validate(res.data[0]) if res.data else None

    async def list_missions(self, principal_id):
        res = await (await self._t("missions")).select("*").eq("principal_id", principal_id) \
            .order("updated_at", desc=True).limit(100).execute()
        return [Mission.model_validate(r) for r in res.data]

    async def delete_mission(self, mission_id):
        await (await self._t("missions")).delete().eq("id", mission_id).execute()

    async def count_created_today(self, principal_id, kind):
        day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if kind == "missions":
            q = (await self._t("missions")).select("id", count="exact", head=True) \
                .eq("principal_id", principal_id)
        else:
            q = (await self._t("conversations")) \
                .select("id, missions!inner(principal_id)", count="exact", head=True) \
                .eq("missions.principal_id", principal_id)
        res = await q.gte("created_at", day).execute()
        return res.count or 0

    # conversations -------------------------------------------------------------------
    async def save_conversation(self, conv):
        await (await self._t("conversations")).upsert(_j(conv)).execute()
        return conv

    async def get_conversation(self, conversation_id):
        res = await (await self._t("conversations")).select("*").eq("id", conversation_id) \
            .limit(1).execute()
        return Conversation.model_validate(res.data[0]) if res.data else None

    async def list_conversations(self, mission_ids):
        if not mission_ids:
            return []
        res = await (await self._t("conversations")).select("*") \
            .in_("mission_id", mission_ids).execute()
        return [Conversation.model_validate(r) for r in res.data]

    async def add_event(self, event):
        await (await self._t("transcript_events")).insert(_j(event)).execute()

    async def update_event_meta(self, event_id, meta):
        await (await self._t("transcript_events")).update({"meta": meta}).eq("id", event_id) \
            .execute()

    async def list_events(self, conversation_id):
        res = await (await self._t("transcript_events")).select("*") \
            .eq("conversation_id", conversation_id).order("ts").execute()
        return [TranscriptEvent.model_validate(r) for r in res.data]

    async def save_approval(self, approval):
        row = _j(approval)
        row.pop("request_en", None)
        row.pop("proposed_en", None)
        await (await self._t("approvals")).upsert(row).execute()

    async def list_approvals(self, conversation_id):
        res = await (await self._t("approvals")).select("*") \
            .eq("conversation_id", conversation_id).order("created_at").execute()
        return [Approval.model_validate(r) for r in res.data]

    async def save_report(self, report):
        await (await self._t("outcome_reports")).upsert({
            "conversation_id": report.conversation_id, "report": _j(report),
            "objective_achieved": report.objective_achieved}).execute()

    async def get_report(self, conversation_id):
        res = await (await self._t("outcome_reports")).select("report") \
            .eq("conversation_id", conversation_id).limit(1).execute()
        return OutcomeReport.model_validate(res.data[0]["report"]) if res.data else None

    # billing -------------------------------------------------------------------------
    async def get_trial_mission(self, principal_id):
        res = await (await self._t("principals")).select("trial_mission_id") \
            .eq("id", principal_id).limit(1).execute()
        return res.data[0].get("trial_mission_id") if res.data else None

    async def set_trial_mission(self, principal_id, mission_id):
        await (await self._t("principals")).update({"trial_mission_id": mission_id}) \
            .eq("id", principal_id).execute()

    async def get_subscription(self, principal_id):
        res = await (await self._t("subscriptions")).select("*") \
            .eq("principal_id", principal_id).limit(1).execute()
        return Subscription.model_validate(res.data[0]) if res.data else None

    async def find_subscription(self, razorpay_subscription_id):
        res = await (await self._t("subscriptions")).select("*") \
            .eq("razorpay_subscription_id", razorpay_subscription_id).limit(1).execute()
        return Subscription.model_validate(res.data[0]) if res.data else None

    async def save_subscription(self, sub):
        await (await self._t("subscriptions")).upsert(_j(sub)).execute()

    async def talk_seconds(self, principal_id, *, since=None, mission_id=None):
        q = (await self._t("conversations")) \
            .select("talk_seconds, missions!inner(principal_id)") \
            .eq("missions.principal_id", principal_id)
        if since is not None:
            q = q.gte("created_at", since.isoformat())
        if mission_id is not None:
            q = q.eq("mission_id", mission_id)
        res = await q.execute()
        return sum(int(r.get("talk_seconds") or 0) for r in res.data)
