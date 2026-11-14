"""Create the monthly Razorpay plan once (per key set: test, then live) and print its id.

    uv run python scripts/create_plan.py           # uses RAZORPAY_KEY_ID/SECRET from .env

Put the printed id in RAZORPAY_PLAN_ID.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.providers.payments.razorpay import Razorpay


async def main() -> None:
    s = get_settings()
    if not (s.razorpay_key_id and s.razorpay_key_secret):
        sys.exit("Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env first.")
    plan = await Razorpay(s).create_plan(amount_paise=s.plan_price_inr * 100, name="Envoy")
    mode = "test" if s.razorpay_key_id.startswith("rzp_test_") else "live"
    print(f"Created {mode} plan: ₹{s.plan_price_inr}/month")
    print(f"RAZORPAY_PLAN_ID={plan['id']}")


if __name__ == "__main__":
    asyncio.run(main())
