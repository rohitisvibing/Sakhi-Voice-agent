"""Google sign-in through Supabase Auth: tokens, per-user data, and ownership."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import auth
from app.main import app

from .conftest import RENT_REQUEST

ISSUER = "https://test.supabase.co/auth/v1"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StubJWKS:
    def get_signing_key_from_jwt(self, _token):
        return jwt.PyJWK.from_dict(
            jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True) | {"alg": "RS256"}
        )


def token(
    sub: str,
    *,
    name="Ananya Iyer",
    email="ananya@example.com",
    exp=3600,
    key=KEY,
    iss=ISSUER,
    aud="authenticated",
) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "email": email,
            "aud": aud,
            "iss": iss,
            "exp": int(time.time()) + exp,
            "user_metadata": {"full_name": name, "avatar_url": "https://x/a.png"},
        },
        key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )


def H(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client(container):
    container.settings.supabase_url = "https://test.supabase.co"
    v = auth.TokenVerifier(container.settings)
    v._jwks = _StubJWKS()
    auth.set_verifier(v)
    yield TestClient(app)
    auth.set_verifier(None)


def _prepared_conversation(client, h) -> tuple[str, str]:
    m = client.post("/api/missions/extract", json={"text": RENT_REQUEST}, headers=h).json()
    client.post(f"/api/missions/{m['id']}/approve", headers=h)
    client.post(f"/api/missions/{m['id']}/prepare", headers=h)
    conv = client.post("/api/conversations", json={"mission_id": m["id"]}, headers=h).json()
    return m["id"], conv["id"]


def test_open_when_supabase_is_not_configured(container):
    c = TestClient(app)
    assert c.get("/api/auth/session").json() == {"required": False, "user": None}
    assert c.get("/api/missions").status_code == 200


def test_needs_a_valid_token(client):
    r = client.get("/api/missions")
    assert r.status_code == 401 and r.json()["error"]["kind"] == "unauthorized"
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/session").json() == {"required": True, "user": None}
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for bad in (
        token(A, key=other),
        token(A, exp=-60),
        token(A, iss="https://evil/auth/v1"),
        token(A, aud="anon"),
        "not-a-jwt",
    ):
        assert client.get("/api/missions", headers=H(bad)).status_code == 401
    expired = client.get("/api/missions", headers=H(token(A, exp=-60))).json()
    assert "expired" in expired["error"]["message"]


def test_first_sign_in_creates_a_prefilled_profile(client, container):
    h = H(token(A))
    s = client.get("/api/auth/session", headers=h).json()
    assert s["user"]["email"] == "ananya@example.com" and s["user"]["id"] == A
    assert client.get("/api/missions", headers=h).json() == []
    profile = client.get("/api/profile", headers=h).json()
    values = {f["key"]: f["value"] for f in profile["fields"]}
    assert profile["id"] == A
    assert values["full_name"] == "Ananya Iyer" and values["email"] == "ananya@example.com"


def test_each_user_sees_only_their_own(client):
    ha, hb = H(token(A)), H(token(B, name="Bo", email="bo@example.com"))
    mid, cid = _prepared_conversation(client, ha)
    assert [m["id"] for m in client.get("/api/missions", headers=ha).json()] == [mid]
    assert client.get("/api/missions", headers=hb).json() == []
    for path in (
        f"/api/missions/{mid}",
        f"/api/conversations/{cid}",
        f"/api/conversations/{cid}/report",
    ):
        assert client.get(path, headers=hb).status_code == 404, path
    for path, body in (
        (f"/api/missions/{mid}/approve", None),
        (f"/api/missions/{mid}/prepare", None),
        (f"/api/missions/{mid}/refine", {"answers": {"q": "a"}}),
        ("/api/conversations", {"mission_id": mid}),
        (f"/api/conversations/{cid}/approvals/x", {"decision": "approved"}),
    ):
        assert client.post(path, json=body, headers=hb).status_code == 404, path
    assert client.delete(f"/api/missions/{mid}", headers=hb).status_code == 404
    assert client.get(f"/api/missions/{mid}", headers=ha).status_code == 200


def test_profile_cannot_take_over_someone_elses_field(client):
    ha, hb = H(token(A)), H(token(B, name="Bo", email="bo@example.com"))
    a_field = client.get("/api/profile", headers=ha).json()["fields"][0]
    b = client.get("/api/profile", headers=hb).json()
    b["fields"].append(a_field | {"key": "stolen", "label": "Stolen", "value": "x"})
    saved = client.put("/api/profile", json={"fields": b["fields"]}, headers=hb).json()
    assert a_field["id"] not in {f["id"] for f in saved["fields"]}
    assert client.get("/api/profile", headers=ha).json()["fields"][0] == a_field


def test_websocket_signs_in_with_its_first_frame(client):
    ha = H(token(A))
    _, cid = _prepared_conversation(client, ha)

    with (
        pytest.raises(WebSocketDisconnect) as e,
        client.websocket_connect(f"/ws/conversations/{cid}") as ws,
    ):
        ws.send_json({"type": "start"})  # no auth frame
        ws.receive_json()
        ws.receive_json()
    assert e.value.code == 4401

    with (
        pytest.raises(WebSocketDisconnect) as e,
        client.websocket_connect(f"/ws/conversations/{cid}") as ws,
    ):
        ws.send_json({"type": "auth", "token": token(B, name="Bo")})
        assert ws.receive_json()["error"]["kind"] == "not_found"
        ws.receive_json()
    assert e.value.code == 4404

    with client.websocket_connect(f"/ws/conversations/{cid}") as ws:
        ws.send_json({"type": "auth", "token": ha["Authorization"][7:]})
        assert ws.receive_json()["type"] == "snapshot"


def test_daily_caps(client, container):
    container.settings.max_conversations_per_day = 2
    h = H(token(A))
    mid, _ = _prepared_conversation(client, h)
    assert client.post("/api/conversations", json={"mission_id": mid}, headers=h).status_code == 200
    r = client.post("/api/conversations", json={"mission_id": mid}, headers=h)
    assert r.status_code == 429 and "Daily limit" in r.json()["error"]["message"]
    container.settings.max_missions_per_day = 1
    r = client.post("/api/missions/extract", json={"text": RENT_REQUEST}, headers=h)
    assert r.status_code == 429
