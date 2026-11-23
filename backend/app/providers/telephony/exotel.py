"""Exotel outbound calls with a two-way media stream (AgentStream / Voicebot).

Only the backend talks to Exotel; the key and token never reach the browser. A call is placed
with `Calls/connect`, and Exotel then opens a WebSocket to our stream URL carrying the call
audio both ways (see app/realtime/telephony.py).
"""

import asyncio
import logging

import httpx

from app.core.config import Settings
from app.core.errors import AppError, ErrorKind

log = logging.getLogger(__name__)


def _message(status: int, body: str) -> str:
    text = body.lower()
    if "kyc" in text:
        return "Your Exotel account needs KYC before it can call this number."
    if "whitelist" in text or "verified" in text or "trial" in text or "dnd" in text:
        return ("Exotel won't call this number yet. On a trial account, add it to your "
                "verified numbers first.")
    if status in (401, 403):
        return "Exotel rejected the account credentials. Check the Exotel key and token."
    return f"Exotel couldn't place the call ({status})."


class Exotel:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.base = f"https://{s.exotel_subdomain}/v1/Accounts/{s.exotel_sid}"

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.s.exotel_api_key, self.s.exotel_api_token)

    async def connect_call(self, *, to: str, stream_url: str, status_url: str,
                           time_limit: int, timeout: int, custom_field: str = "") -> dict:
        data = {
            "From": to,
            "CallerId": self.s.exotel_caller_id,
            "CallType": "trans",
            "StreamUrl": stream_url,
            "StreamType": "bidirectional",
            "StatusCallback": status_url,
            "StatusCallbackEvents[0]": "answered",
            "StatusCallbackEvents[1]": "terminal",
            "StatusCallbackContentType": "application/json",
            "TimeLimit": str(time_limit),
            "TimeOut": str(timeout),
            "CustomField": custom_field[:128],
        }
        last: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15, auth=self._auth()) as http:
                    r = await http.post(f"{self.base}/Calls/connect.json", data=data)
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code >= 500:
                last = AppError(ErrorKind.upstream, _message(r.status_code, r.text),
                                provider="Exotel", retryable=True)
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code >= 400:
                log.warning("Exotel connect refused (%s): %s", r.status_code, r.text[:300])
                raise AppError(ErrorKind.upstream, _message(r.status_code, r.text),
                               provider="Exotel")
            call = r.json().get("Call") or {}
            return {"Sid": call.get("Sid"), "Status": call.get("Status")}
        if isinstance(last, AppError):
            raise last
        raise AppError(ErrorKind.upstream, "Couldn't reach Exotel. Try again.", provider="Exotel",
                       retryable=True)

    async def get_call(self, sid: str) -> dict:
        async with httpx.AsyncClient(timeout=10, auth=self._auth()) as http:
            r = await http.get(f"{self.base}/Calls/{sid}.json")
        if r.status_code >= 400:
            raise AppError(ErrorKind.upstream, _message(r.status_code, r.text), provider="Exotel")
        return r.json().get("Call") or {}


class LocalExotel:
    """EXOTEL_SIMULATE=true: no real call. The stream URL is logged so the local simulator
    (scripts/exotel_sim.py) can play the person being called."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def connect_call(self, **kw) -> dict:
        self.calls.append(kw)
        sid = f"sim_{len(self.calls)}"
        log.warning("SIMULATED CALL %s to %s: stream_url=%s", sid, kw.get("to"),
                    kw.get("stream_url"))
        return {"Sid": sid, "Status": "queued"}

    async def get_call(self, sid: str) -> dict:
        return {"Sid": sid, "Status": "in-progress"}
