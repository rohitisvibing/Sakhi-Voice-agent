"""Subscription state mirrored from Razorpay."""

from datetime import datetime

from pydantic import Field

from .common import Schema, utcnow

# Razorpay subscription statuses that grant access while the period lasts.
ACTIVE_STATUSES = {"active", "authenticated", "pending"}


class Subscription(Schema):
    principal_id: str
    razorpay_subscription_id: str
    status: str
    current_start: datetime | None = None
    current_end: datetime | None = None
    cancel_at_period_end: bool = False
    updated_at: datetime = Field(default_factory=utcnow)

    def grants_access(self, now: datetime) -> bool:
        if self.current_end is None:
            return self.status == "active"
        # Cancelled (at period end) and halted subscriptions still run to the period's end.
        return now < self.current_end and (
            self.status in ACTIVE_STATUSES or self.status in {"cancelled", "halted"})
