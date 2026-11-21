"""Create (or reset) the email-and-password test account for Razorpay's website review.

    uv run python scripts/create_test_account.py                  # reviewer@sakhi.buzz
    uv run python scripts/create_test_account.py someone@sakhi.buzz

Prints the email and a freshly generated password once; nothing is written to disk.
Running it again resets the password, so a lost or leaked one is replaced by rerunning.
"""

import asyncio
import secrets
import string
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings

EMAIL = "reviewer@sakhi.buzz"
NAME = "Razorpay Reviewer"


def new_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(
                c.islower() for c in pw):
            return pw


async def main() -> None:
    s = get_settings()
    if not (s.supabase_url and s.supabase_service_role_key):
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env first.")
    email = (sys.argv[1] if len(sys.argv) > 1 else EMAIL).strip().lower()
    password = new_password()
    key = s.supabase_service_role_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    base = s.supabase_url.rstrip("/") + "/auth/v1/admin/users"
    async with httpx.AsyncClient(timeout=20, headers=headers) as http:
        existing = None
        page = 1
        while existing is None:
            r = await http.get(base, params={"page": page, "per_page": 200})
            r.raise_for_status()
            users = r.json().get("users", [])
            existing = next((u for u in users if (u.get("email") or "").lower() == email), None)
            if len(users) < 200:
                break
            page += 1
        body = {"password": password, "email_confirm": True,
                "user_metadata": {"full_name": NAME}}
        if existing:
            r = await http.put(f"{base}/{existing['id']}", json=body)
            action = "Reset the password for"
        else:
            r = await http.post(base, json={"email": email, **body})
            action = "Created"
        if r.status_code >= 400:
            sys.exit(f"Supabase refused ({r.status_code}): {r.text[:200]}")
    print(f"{action} {email}")
    print(f"Email:    {email}")
    print(f"Password: {password}")


if __name__ == "__main__":
    asyncio.run(main())
