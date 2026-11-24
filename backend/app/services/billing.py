"""Who may create missions and start calls, and how much talk time is left.

- Billing off (no Razorpay keys): everything is open.
- Trial: the first mission is free, with all its calls, up to a hidden talk-time cap.
- Subscribed: any mission; calls while this period's talk time is under the monthly cap.
Limits are enforced here and never sent to the UI.
"""

from datetime import datetime

from app.core.deps import Container
from app.core.errors import AppError, ErrorKind
from app.models.billing import Subscription
from app.models.common import utcnow
from app.models.mission import Mission
from app.providers.payments.razorpay import ts


def enabled(c: Container) -> bool:
    return c.settings.billing_configured


async def is_free(c: Container, pid: str) -> bool:
    """Accounts listed in FREE_ACCOUNTS never pay and have no talk-time cap."""
    if not c.settings.free_emails:
        return False
    from app.core.auth import verified_email  # auth imports this module

    return (await verified_email(c, pid) or "") in c.settings.free_emails


async def _billed(c: Container, pid: str) -> bool:
    return enabled(c) and not await is_free(c, pid)


def needs_subscription(message: str = "Subscribe to keep Sakhi working for you.") -> AppError:
    return AppError(ErrorKind.payment_required, message, detail={"reason": "subscribe"})


def out_of_talk_time(renews_on: datetime | None) -> AppError:
    when = f" It renews on {renews_on:%-d %b}." if renews_on else ""
    return AppError(ErrorKind.payment_required, "You've used this month's talk time." + when,
                    detail={"reason": "allowance",
                            "renews_on": renews_on.isoformat() if renews_on else None})


async def active_subscription(c: Container, pid: str) -> Subscription | None:
    sub = await c.repo.get_subscription(pid)
    return sub if sub and sub.grants_access(utcnow()) else None


async def can_create_mission(c: Container, pid: str) -> bool:
    """Raises unless allowed. Returns True when this mission becomes the free trial."""
    if not await _billed(c, pid) or await active_subscription(c, pid):
        return False
    if await c.repo.get_trial_mission(pid) is None:
        return True
    raise needs_subscription()


async def remaining_seconds(c: Container, mission: Mission) -> float | None:
    """Talk time this mission's calls may still use; None = unmetered (billing off)."""
    pid = mission.principal_id
    if not await _billed(c, pid):
        return None
    sub = await active_subscription(c, pid)
    if sub:
        used = await c.repo.talk_seconds(pid, since=sub.current_start)
        return max(0.0, c.settings.monthly_talk_minutes * 60 - used)
    if await c.repo.get_trial_mission(pid) == mission.id:
        used = await c.repo.talk_seconds(pid, mission_id=mission.id)
        return max(0.0, c.settings.trial_talk_minutes * 60 - used)
    return 0.0


async def check_can_start_call(c: Container, mission: Mission) -> None:
    left = await remaining_seconds(c, mission)
    if left is None or left > 0:
        return
    sub = await active_subscription(c, mission.principal_id)
    if sub:
        raise out_of_talk_time(sub.current_end)
    raise needs_subscription()


async def status(c: Container, pid: str) -> dict:
    """What the UI may know: plan state and dates, never minutes."""
    out = {"enabled": enabled(c), "price_inr": c.settings.plan_price_inr,
           "key_id": c.settings.razorpay_key_id or None, "renews_on": None,
           "cancel_at_period_end": False}
    if not enabled(c):
        return out | {"status": "free"}
    if await is_free(c, pid):
        return out | {"enabled": False, "status": "free"}  # no plan, trial or paywall to show
    sub = await active_subscription(c, pid)
    if sub:
        used = await c.repo.talk_seconds(pid, since=sub.current_start)
        exhausted = used >= c.settings.monthly_talk_minutes * 60
        return out | {"status": "exhausted" if exhausted else "active",
                      "renews_on": sub.current_end.isoformat() if sub.current_end else None,
                      "cancel_at_period_end": sub.cancel_at_period_end}
    trial = await c.repo.get_trial_mission(pid)
    return out | {"status": "trial" if trial is None else "trial_used", "trial_mission_id": trial}


def subscription_from_razorpay(pid: str, data: dict,
                               previous: Subscription | None = None) -> Subscription:
    """Our copy of a Razorpay subscription. A cancel-at-period-end we asked for sticks until
    Razorpay reports the subscription cancelled or completed."""
    status = data.get("status", "")
    keep_flag = bool(previous and previous.cancel_at_period_end
                     and previous.razorpay_subscription_id == data["id"])
    return Subscription(
        principal_id=pid, razorpay_subscription_id=data["id"], status=status,
        current_start=ts(data.get("current_start")) or (previous and previous.current_start),
        current_end=ts(data.get("current_end")) or (previous and previous.current_end),
        cancel_at_period_end=keep_flag or status in ("cancelled", "completed"),
        updated_at=utcnow())
