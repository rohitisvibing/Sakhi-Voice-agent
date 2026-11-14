"""Razorpay Subscriptions API (server side) and signature checks."""

import hashlib
import hmac
from datetime import UTC, datetime

import httpx

from app.core.config import Settings
from app.core.errors import from_http_status

API = "https://api.razorpay.com/v1"


def _hmac(secret: str, message: str | bytes) -> str:
    data = message.encode() if isinstance(message, str) else message
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def checkout_signature_ok(secret: str, payment_id: str, subscription_id: str,
                          signature: str) -> bool:
    """Checkout's handler signature for a subscription payment."""
    return hmac.compare_digest(_hmac(secret, f"{payment_id}|{subscription_id}"), signature)


def webhook_signature_ok(secret: str, body: bytes, signature: str) -> bool:
    return bool(secret and signature) and hmac.compare_digest(_hmac(secret, body), signature)


def ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


class Razorpay:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _call(self, method: str, path: str, json: dict | None = None) -> dict:
        auth = (self.settings.razorpay_key_id, self.settings.razorpay_key_secret)
        async with httpx.AsyncClient(timeout=20, auth=auth) as client:
            r = await client.request(method, API + path, json=json)
        if r.status_code >= 400:
            raise from_http_status("Razorpay", r.status_code, r.text)
        return r.json()

    async def create_plan(self, *, amount_paise: int, name: str) -> dict:
        return await self._call("POST", "/plans", {
            "period": "monthly", "interval": 1,
            "item": {"name": name, "amount": amount_paise, "currency": "INR"}})

    async def create_subscription(self, *, principal_id: str, email: str | None) -> dict:
        notes = {"principal_id": principal_id} | ({"email": email} if email else {})
        return await self._call("POST", "/subscriptions", {
            "plan_id": self.settings.razorpay_plan_id, "total_count": 120,
            "customer_notify": 1, "notes": notes})

    async def get_subscription(self, subscription_id: str) -> dict:
        return await self._call("GET", f"/subscriptions/{subscription_id}")

    async def cancel_subscription(self, subscription_id: str) -> dict:
        return await self._call("POST", f"/subscriptions/{subscription_id}/cancel",
                                {"cancel_at_cycle_end": 1})
