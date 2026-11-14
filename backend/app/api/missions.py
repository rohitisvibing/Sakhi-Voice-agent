from fastapi import APIRouter

from app.api.ownership import owned_mission
from app.api.profiles import Principal, load_profile
from app.core.auth import check_daily_limit
from app.core.deps import get_container
from app.core.errors import AppError, ErrorKind
from app.models.common import Schema, utcnow
from app.models.conversation import ConversationState
from app.models.mission import (
    AgentTemplate,
    ExtractionResult,
    ExtractRequest,
    Mission,
    MissionSpec,
    MissionStatus,
    RefineRequest,
)
from app.services import billing, objective_engine, strategy_engine

router = APIRouter(prefix="/api", tags=["missions"])

AGENT_TYPES = {"negotiat": "Negotiator", "cancel": "Customer support", "book": "Scheduler",
               "appointment": "Scheduler", "complain": "Customer support"}


def _agent_type(text: str) -> str:
    low = text.lower()
    return next((v for k, v in AGENT_TYPES.items() if k in low), "Negotiator")


class MissionSummary(Schema):
    id: str
    title: str
    objective: str
    status: MissionStatus
    agent_type: str
    last_activity: str
    conversation_id: str | None = None
    conversation_state: ConversationState | None = None
    objective_met: bool | None = None


async def _get(mission_id: str, pid: str) -> Mission:
    return await owned_mission(get_container(), mission_id, pid)


@router.get("/templates", response_model=list[AgentTemplate])
async def templates() -> list[AgentTemplate]:
    return await get_container().repo.list_templates()


@router.get("/missions", response_model=list[MissionSummary])
async def list_missions(pid: Principal) -> list[MissionSummary]:
    c = get_container()
    missions = await c.repo.list_missions(pid)
    convs = await c.repo.list_conversations([m.id for m in missions])
    latest: dict = {}
    for conv in sorted(convs, key=lambda x: x.updated_at):
        latest[conv.mission_id] = conv
    out = []
    for m in missions:
        conv = latest.get(m.id)
        last = max(m.updated_at, conv.updated_at) if conv else m.updated_at
        out.append(MissionSummary(
            id=m.id, title=m.spec.title, objective=m.spec.objective, status=m.status,
            agent_type=m.agent_type, last_activity=last.isoformat(),
            conversation_id=conv.id if conv else None,
            conversation_state=conv.state if conv else None,
            objective_met=conv.negotiation.objective_met if conv else None))
    return out


@router.post("/missions/extract", response_model=Mission)
async def extract(body: ExtractRequest, pid: Principal) -> Mission:
    c = get_container()
    await check_daily_limit(c, pid, "missions")
    is_trial = await billing.can_create_mission(c, pid)
    profile = await load_profile(pid)
    result = await objective_engine.extract(c.llm, body, profile)
    mission = Mission(principal_id=profile.id, raw_input=body.text, spec=result.spec,
                      extraction=result, agent_type=_agent_type(body.text),
                      template_id=body.template_id)
    saved = await c.repo.save_mission(mission)
    if is_trial:
        await c.repo.set_trial_mission(pid, saved.id)
    return saved


@router.get("/missions/{mission_id}", response_model=Mission)
async def get_mission(mission_id: str, pid: Principal) -> Mission:
    return await _get(mission_id, pid)


@router.post("/missions/{mission_id}/refine", response_model=Mission)
async def refine(mission_id: str, body: RefineRequest, pid: Principal) -> Mission:
    c = get_container()
    m = await _get(mission_id, pid)
    answers = {q: a for q, a in body.answers.items() if a.strip()}
    if not answers:
        return m
    result: ExtractionResult = await objective_engine.refine(c.llm, m.spec, m.raw_input, answers)
    m.spec, m.extraction = result.spec, result
    m.raw_input += "\n\n" + "\n".join(f"{q} {a}" for q, a in answers.items())
    m.updated_at = utcnow()
    return await c.repo.save_mission(m)


@router.put("/missions/{mission_id}/spec", response_model=Mission)
async def update_spec(mission_id: str, spec: MissionSpec, pid: Principal) -> Mission:
    m = await _get(mission_id, pid)
    if m.status not in (MissionStatus.draft, MissionStatus.approved, MissionStatus.prepared):
        raise AppError(ErrorKind.invalid_state, "This mission is already running.")
    m.spec = objective_engine._post_validate(
        ExtractionResult(spec=spec, **(m.extraction.model_dump(exclude={"spec"})
                                       if m.extraction else {}))).spec
    m.status, m.brief, m.updated_at = MissionStatus.draft, None, utcnow()
    return await get_container().repo.save_mission(m)


@router.post("/missions/{mission_id}/approve", response_model=Mission)
async def approve(mission_id: str, pid: Principal) -> Mission:
    m = await _get(mission_id, pid)
    if m.status == MissionStatus.draft:
        m.status, m.updated_at = MissionStatus.approved, utcnow()
        await get_container().repo.save_mission(m)
    return m


@router.post("/missions/{mission_id}/prepare", response_model=Mission)
async def prepare(mission_id: str, pid: Principal) -> Mission:
    c = get_container()
    m = await _get(mission_id, pid)
    if m.status == MissionStatus.draft:
        raise AppError(ErrorKind.invalid_state, "Approve the mission before preparing the agent.")
    profile = await load_profile(pid)
    m.brief = await strategy_engine.prepare(c.llm, m.spec, profile)
    if m.status == MissionStatus.approved:
        m.status = MissionStatus.prepared
    m.updated_at = utcnow()
    return await c.repo.save_mission(m)


@router.delete("/missions/{mission_id}", status_code=204)
async def delete(mission_id: str, pid: Principal) -> None:
    await _get(mission_id, pid)
    await get_container().repo.delete_mission(mission_id)
