"""Move the data made before sign-in existed (the single default principal) to a real account.

    uv run python scripts/claim_data.py you@example.com            # missions (and their calls)
    uv run python scripts/claim_data.py you@example.com --profile  # also replace your profile

The account must have signed in once. Run from backend/, with .env pointing at the project.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from supabase import acreate_client

from app.core.config import get_settings


async def main(email: str, with_profile: bool, dry_run: bool) -> None:
    s = get_settings()
    db = await acreate_client(s.supabase_url, s.supabase_service_role_key)
    legacy = s.default_principal_id

    user, page = None, 1
    while user is None:
        users = await db.auth.admin.list_users(page=page, per_page=200)
        if not users:
            break
        user = next((u for u in users if (u.email or "").lower() == email.lower()), None)
        page += 1
    if user is None:
        sys.exit(f"No account for {email}. Sign in to Envoy with it once, then rerun.")
    if not (await db.table("principals").select("id").eq("id", user.id).execute()).data:
        sys.exit(f"{email} hasn't opened Envoy yet. Sign in once, then rerun.")

    missions = (await db.table("missions").select("id,title").eq("principal_id", legacy)
                .execute()).data
    print(f"{len(missions)} missions to move to {email}:")
    for m in missions:
        print("  -", m["title"])
    if with_profile:
        print("and the old profile replaces the current one.")
    if dry_run:
        return

    if missions:
        await db.table("missions").update({"principal_id": user.id}) \
            .eq("principal_id", legacy).execute()
    if with_profile:
        old = (await db.table("principals").select("*").eq("id", legacy).execute()).data
        if old:
            row = old[0]
            await db.table("principals").update({
                k: row[k] for k in ("preferred_languages", "communication_preferences",
                                    "background")}).eq("id", user.id).execute()
            await db.table("profile_fields").delete().eq("principal_id", user.id).execute()
            await db.table("profile_fields").update({"principal_id": user.id}) \
                .eq("principal_id", legacy).execute()
    print("Done.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("email")
    p.add_argument("--profile", action="store_true", help="also move the old profile")
    p.add_argument("--dry-run", action="store_true", help="show what would move")
    a = p.parse_args()
    asyncio.run(main(a.email, a.profile, a.dry_run))
