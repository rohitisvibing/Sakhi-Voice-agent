from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import current_principal
from app.core.deps import get_container
from app.models.common import new_id
from app.models.principal import PrincipalProfile, ProfileUpdate

router = APIRouter(prefix="/api/profile", tags=["profile"])


Principal = Annotated[str, Depends(current_principal)]


async def load_profile(pid: str) -> PrincipalProfile:
    c = get_container()
    profile = await c.repo.get_profile(pid)
    if profile is None:
        profile = await c.repo.save_profile(PrincipalProfile.blank(pid))
    return profile


@router.get("", response_model=PrincipalProfile)
async def get_profile(pid: Principal) -> PrincipalProfile:
    return await load_profile(pid)


@router.put("", response_model=PrincipalProfile)
async def update_profile(body: ProfileUpdate, pid: Principal) -> PrincipalProfile:
    profile = await load_profile(pid)
    own_ids = {f.id for f in profile.fields}
    data = body.model_dump(exclude_unset=True)
    updated = profile.model_copy(update={k: v for k, v in data.items() if k != "fields"})
    if body.fields is not None:
        seen: set[str] = set()
        fields = []
        for f in body.fields:
            f.key = f.key.strip().lower().replace(" ", "_") or f.label.lower().replace(" ", "_")
            if f.key in seen:
                continue
            seen.add(f.key)
            if f.id not in own_ids:
                f.id = new_id()  # never adopt (and overwrite) a row that isn't this profile's
            f.value = (f.value or "").strip() or None
            fields.append(f)
        updated.fields = fields
    return await get_container().repo.save_profile(updated)
