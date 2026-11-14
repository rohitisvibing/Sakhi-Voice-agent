"""Sign-in through Supabase Auth (Google): every API call carries a Supabase access token.

The token's subject (the auth.users id) is the principal id, so each person has their own
profile, missions and calls. Tokens are verified locally against the project's signing keys
(JWKS); legacy HS256 projects use SUPABASE_JWT_SECRET, or ask Supabase Auth directly.

With Supabase unconfigured (demo, tests) or AUTH_DISABLED=true, everyone is the single
default principal, as before.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import APIRouter, Request

from app.core.config import Settings
from app.core.deps import Container, get_container
from app.core.errors import AppError, ErrorKind
from app.models.principal import PrincipalProfile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None = None
    name: str | None = None
    avatar_url: str | None = None


def auth_required(settings: Settings) -> bool:
    return bool(settings.supabase_url) and not settings.auth_disabled


def unauthorized(message: str = "Sign in to continue.") -> AppError:
    return AppError(ErrorKind.unauthorized, message)


def _user_from_claims(claims: dict) -> AuthUser:
    meta = claims.get("user_metadata") or {}
    return AuthUser(id=claims["sub"], email=claims.get("email") or meta.get("email"),
                    name=meta.get("full_name") or meta.get("name"),
                    avatar_url=meta.get("avatar_url") or meta.get("picture"))


class TokenVerifier:
    """Verifies Supabase access tokens. Swappable in tests via `set_verifier`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
        self._jwks = jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True,
                                     lifespan=3600)
        self._remote: dict[str, tuple[float, AuthUser]] = {}

    async def verify(self, token: str) -> AuthUser:
        try:
            alg = jwt.get_unverified_header(token).get("alg", "")
        except jwt.PyJWTError as e:
            raise unauthorized() from e
        if alg not in ("HS256", "RS256", "ES256", "EdDSA"):
            raise unauthorized()
        try:
            if alg == "HS256":
                if not self.settings.supabase_jwt_secret:
                    return await self._ask_supabase(token)
                key = self.settings.supabase_jwt_secret
            else:
                key = (await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)).key
            claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated",
                                issuer=self.issuer, options={"require": ["exp", "sub"]})
        except jwt.ExpiredSignatureError as e:
            raise unauthorized("Your sign-in expired. Sign in again.") from e
        except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
            raise unauthorized() from e
        return _user_from_claims(claims)

    async def _ask_supabase(self, token: str) -> AuthUser:
        """Legacy symmetric-key projects without the secret: let Supabase Auth check it."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        hit = self._remote.get(digest)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        apikey = self.settings.supabase_anon_key or self.settings.supabase_service_role_key
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.issuer}/user",
                                 headers={"Authorization": f"Bearer {token}", "apikey": apikey})
        if r.status_code != 200:
            raise unauthorized()
        data = r.json()
        user = _user_from_claims({"sub": data["id"], "email": data.get("email"),
                                  "user_metadata": data.get("user_metadata")})
        if len(self._remote) > 1000:
            self._remote.clear()
        self._remote[digest] = (time.monotonic() + 300, user)
        return user


_verifier: TokenVerifier | None = None


def get_verifier(settings: Settings) -> TokenVerifier:
    global _verifier
    if _verifier is None or _verifier.settings is not settings:
        _verifier = TokenVerifier(settings)
    return _verifier


def set_verifier(v: TokenVerifier | None) -> None:
    global _verifier
    _verifier = v


async def ensure_principal(c: Container, user: AuthUser) -> PrincipalProfile:
    """The principal for a signed-in user, created on first sign-in with name and email filled."""
    profile = await c.repo.get_profile(user.id)
    if profile is not None:
        return profile
    profile = PrincipalProfile.blank(user.id)
    for f in profile.fields:
        if f.key == "full_name" and user.name:
            f.value = user.name
        elif f.key == "email" and user.email:
            f.value = user.email
    log.info("new principal %s", user.id)
    return await c.repo.save_profile(profile)


async def authenticate(c: Container, token: str | None) -> str:
    """Principal id for a bearer token (or the default principal when sign-in is off)."""
    if not auth_required(c.settings):
        return c.settings.default_principal_id
    if not token:
        raise unauthorized()
    user = await get_verifier(c.settings).verify(token)
    await ensure_principal(c, user)
    return user.id


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        return None
    return h[7:].strip() or None


async def current_principal(request: Request) -> str:
    """FastAPI dependency: who is calling."""
    return await authenticate(get_container(), bearer(request))


async def check_daily_limit(c: Container, principal_id: str, kind: str) -> None:
    cap = (c.settings.max_missions_per_day if kind == "missions"
           else c.settings.max_conversations_per_day)
    if cap <= 0 or not auth_required(c.settings):
        return
    used = await c.repo.count_created_today(principal_id, kind)
    if used >= cap:
        noun = "missions" if kind == "missions" else "calls"
        raise AppError(ErrorKind.rate_limited,
                       f"Daily limit reached: {cap} {noun} a day. Try again tomorrow.")


router = APIRouter(prefix="/api/auth")


@router.get("/session")
async def session(request: Request) -> dict:
    c = get_container()
    if not auth_required(c.settings):
        return {"required": False, "user": None}
    token = bearer(request)
    if not token:
        return {"required": True, "user": None}
    try:
        user = await get_verifier(c.settings).verify(token)
    except AppError:
        return {"required": True, "user": None}
    return {"required": True, "user": {"id": user.id, "email": user.email, "name": user.name,
                                       "avatar_url": user.avatar_url}}
