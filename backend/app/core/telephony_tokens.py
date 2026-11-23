"""Signed tokens for the URLs we hand to Exotel (it doesn't sign its own requests).

token = <conversation_id>.<purpose>.<expiry>.<hmac>
"""

import hashlib
import hmac
import time

STREAM, STATUS = "stream", "status"
_used: dict[str, float] = {}


def _sig(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]


def make(secret: str, conversation_id: str, purpose: str, ttl_s: int) -> str:
    body = f"{conversation_id}.{purpose}.{int(time.time()) + ttl_s}"
    return f"{body}.{_sig(secret, body)}"


def check(secret: str, token: str | None, purpose: str, *, single_use: bool = False
          ) -> str | None:
    """The conversation id if the token is genuine, unexpired and for `purpose`."""
    if not secret or not token:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    conv, p, exp, sig = parts
    body = f"{conv}.{p}.{exp}"
    if p != purpose or not hmac.compare_digest(sig, _sig(secret, body)):
        return None
    if not exp.isdigit() or int(exp) < time.time():
        return None
    if single_use:
        now = time.time()
        for k in [k for k, t in _used.items() if t < now]:
            _used.pop(k, None)
        if token in _used:
            return None
        _used[token] = int(exp)
    return conv
