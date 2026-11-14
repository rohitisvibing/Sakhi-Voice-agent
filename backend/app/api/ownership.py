"""Ownership checks: a mission belongs to a principal; a conversation to its mission's principal.

Anything that isn't yours is reported as not found, so ids don't reveal what exists.
"""

from app.core.deps import Container
from app.core.errors import AppError, ErrorKind
from app.models.conversation import Conversation
from app.models.mission import Mission


async def owned_mission(c: Container, mission_id: str, principal_id: str) -> Mission:
    m = await c.repo.get_mission(mission_id)
    if not m or m.principal_id != principal_id:
        raise AppError(ErrorKind.not_found, "Mission not found.")
    return m


async def owned_conversation(c: Container, conversation_id: str, principal_id: str,
                             conv: Conversation | None = None) -> tuple[Conversation, Mission]:
    conv = conv or await c.repo.get_conversation(conversation_id)
    if not conv:
        raise AppError(ErrorKind.not_found, "Conversation not found.")
    m = await c.repo.get_mission(conv.mission_id)
    if not m or m.principal_id != principal_id:
        raise AppError(ErrorKind.not_found, "Conversation not found.")
    return conv, m
