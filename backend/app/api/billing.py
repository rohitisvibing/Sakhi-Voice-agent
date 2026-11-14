"""Subscription endpoints: status, subscribe (Razorpay Checkout), verify, cancel, webhook."""

import json
import logging

from fastapi import APIRouter, Request

from app.api.profiles import Principal, load_profile
from app.core.deps import get_container
from app.core.errors import AppError, ErrorKind
from app.models.common import Schema
from app.providers.payments.razorpay import (
    Razorpay,
    checkout_signature_ok,
    webhook_signature_ok,
)
from app.services import billing

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutResult(Schema):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str


def _rzp() -> Razorpay:
    c = get_container()
    if not billing.enabled(c):
        raise AppError(ErrorKind.invalid_state, "Payments aren't set up.")
    return c.payments or Razorpay(c.settings)


@router.get("")
async def status(pid: Principal) -> dict:
    return await billing.status(get_container(), pid)


@router.post("/subscribe")
async def subscribe(pid: Principal) -> dict:
    c = get_container()
    rzp = _rzp()
    if await billing.active_subscription(c, pid):
        raise AppError(ErrorKind.invalid_state, "You're already subscribed.")
    existing = await c.repo.get_subscription(pid)
    if existing and existing.status == "created":  # checkout opened before, never paid
        return {"subscription_id": existing.razorpay_subscription_id,
                "key_id": c.settings.razorpay_key_id}
    profile = await load_profile(pid)
    email = next((f.value for f in profile.fields if f.key == "email" and f.value), None)
    data = await rzp.create_subscription(principal_id=pid, email=email)
    await c.repo.save_subscription(billing.subscription_from_razorpay(pid, data))
    return {"subscription_id": data["id"], "key_id": c.settings.razorpay_key_id}


@router.post("/verify")
async def verify(body: CheckoutResult, pid: Principal) -> dict:
    c = get_container()
    rzp = _rzp()
    mine = await c.repo.get_subscription(pid)
    if not mine or mine.razorpay_subscription_id != body.razorpay_subscription_id:
        raise AppError(ErrorKind.not_found, "Subscription not found.")
    if not checkout_signature_ok(c.settings.razorpay_key_secret, body.razorpay_payment_id,
                                 body.razorpay_subscription_id, body.razorpay_signature):
        raise AppError(ErrorKind.invalid_state, "The payment couldn't be verified.")
    data = await rzp.get_subscription(body.razorpay_subscription_id)
    await c.repo.save_subscription(billing.subscription_from_razorpay(pid, data, mine))
    return await billing.status(c, pid)


@router.post("/cancel")
async def cancel(pid: Principal) -> dict:
    c = get_container()
    rzp = _rzp()
    sub = await billing.active_subscription(c, pid)
    if not sub:
        raise AppError(ErrorKind.invalid_state, "There's no active subscription.")
    data = await rzp.cancel_subscription(sub.razorpay_subscription_id)
    sub = billing.subscription_from_razorpay(pid, data, sub)
    sub.cancel_at_period_end = True
    await c.repo.save_subscription(sub)
    return await billing.status(c, pid)


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request) -> dict:
    """Razorpay's server calls this; it signs the raw body. No user sign-in here."""
    c = get_container()
    body = await request.body()
    if not webhook_signature_ok(c.settings.razorpay_webhook_secret, body,
                                request.headers.get("x-razorpay-signature", "")):
        raise AppError(ErrorKind.unauthorized, "Bad signature.")
    event = json.loads(body)
    entity = (event.get("payload", {}).get("subscription") or {}).get("entity")
    if not entity or not str(event.get("event", "")).startswith("subscription."):
        return {"ok": True}
    known = await c.repo.find_subscription(entity["id"])
    pid = (known.principal_id if known else None) or (entity.get("notes") or {}).get(
        "principal_id")
    if not pid:
        log.warning("webhook for unknown subscription %s", entity["id"])
        return {"ok": True}
    await c.repo.save_subscription(billing.subscription_from_razorpay(pid, entity, known))
    log.info("subscription %s -> %s (%s)", entity["id"], entity.get("status"), event["event"])
    return {"ok": True}
