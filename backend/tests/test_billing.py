"""Razorpay subscription, the free trial mission, and metered talk time."""

import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.billing import Subscription
from app.models.common import utcnow
from app.models.conversation import ConversationState as S
from app.realtime import session as session_mod

from .conftest import RENT_REQUEST
from .fakes import ScriptedLLM
from .test_conversation_flow import settle, turn

SECRET, HOOK = "key_secret_test", "hook_secret_test"


class FakeRazorpay:
    def __init__(self):
        self.subs: dict[str, dict] = {}
        self.cancelled: list[str] = []

    async def create_subscription(self, *, principal_id, email):
        sid = f"sub_{len(self.subs) + 1}"
        self.subs[sid] = {"id": sid, "status": "created", "notes": {"principal_id": principal_id}}
        return self.subs[sid]

    async def get_subscription(self, sid):
        return self.subs[sid]

    async def cancel_subscription(self, sid):
        self.cancelled.append(sid)
        return self.subs[sid]

    def pay(self, sid, days=30):
        now = int(time.time())
        self.subs[sid] |= {
            "status": "active",
            "current_start": now,
            "current_end": now + days * 86400,
        }


@pytest.fixture
def rzp(container):
    s = container.settings
    s.razorpay_key_id, s.razorpay_key_secret = "rzp_test_x", SECRET
    s.razorpay_webhook_secret, s.razorpay_plan_id = HOOK, "plan_x"
    container.payments = FakeRazorpay()
    return container.payments


@pytest.fixture
def client(rzp):
    return TestClient(app)


def sign(payment_id, sub_id, secret=SECRET):
    return hmac.new(secret.encode(), f"{payment_id}|{sub_id}".encode(), hashlib.sha256).hexdigest()


def _mission(client):
    return client.post("/api/missions/extract", json={"text": RENT_REQUEST})


def _ready(client, mid):
    client.post(f"/api/missions/{mid}/approve")
    client.post(f"/api/missions/{mid}/prepare")


def _subscribe(client, rzp):
    sid = client.post("/api/billing/subscribe").json()["subscription_id"]
    rzp.pay(sid)
    return sid, client.post(
        "/api/billing/verify",
        json={
            "razorpay_payment_id": "pay_1",
            "razorpay_subscription_id": sid,
            "razorpay_signature": sign("pay_1", sid),
        },
    )


def test_everything_is_free_without_razorpay_keys(container):
    c = TestClient(app)
    assert c.get("/api/billing").json()["status"] == "free"
    for _ in range(3):
        assert _mission(c).status_code == 200


def test_first_mission_is_free_then_payment_is_asked(client):
    assert client.get("/api/billing").json()["status"] == "trial"
    first = _mission(client)
    assert first.status_code == 200
    _ready(client, first.json()["id"])
    assert (
        client.post("/api/conversations", json={"mission_id": first.json()["id"]}).status_code
        == 200
    )
    second = _mission(client)
    assert second.status_code == 402
    err = second.json()["error"]
    assert err["kind"] == "payment_required" and err["reason"] == "subscribe"
    assert client.get("/api/billing").json()["status"] == "trial_used"


def test_trial_calls_stop_at_the_hidden_cap(client, container):
    m = _mission(client).json()
    _ready(client, m["id"])
    conv = client.post("/api/conversations", json={"mission_id": m["id"]}).json()
    stored = container.repo.conversations[conv["id"]]
    stored.talk_seconds = container.settings.trial_talk_minutes * 60
    r = client.post("/api/conversations", json={"mission_id": m["id"]})
    assert r.status_code == 402 and r.json()["error"]["reason"] == "subscribe"


def test_subscribing_unlocks_missions_and_status_hides_minutes(client, rzp):
    _mission(client)
    _, verified = _subscribe(client, rzp)
    body = verified.json()
    assert body["status"] == "active" and body["renews_on"]
    assert not any("minute" in k or "second" in k or "remaining" in k for k in body)
    assert _mission(client).status_code == 200


def test_forged_checkout_signature_is_rejected(client, rzp):
    sid = client.post("/api/billing/subscribe").json()["subscription_id"]
    rzp.pay(sid)
    r = client.post(
        "/api/billing/verify",
        json={
            "razorpay_payment_id": "pay_1",
            "razorpay_subscription_id": sid,
            "razorpay_signature": sign("pay_1", sid, secret="wrong"),
        },
    )
    assert r.status_code == 409
    assert client.get("/api/billing").json()["status"] == "trial"


def test_subscribe_reuses_an_unpaid_checkout(client):
    a = client.post("/api/billing/subscribe").json()["subscription_id"]
    b = client.post("/api/billing/subscribe").json()["subscription_id"]
    assert a == b


def test_monthly_allowance_is_enforced_and_says_when_it_renews(client, rzp, container):
    _mission(client)
    _subscribe(client, rzp)
    m = _mission(client).json()
    _ready(client, m["id"])
    conv = client.post("/api/conversations", json={"mission_id": m["id"]}).json()
    container.repo.conversations[conv["id"]].talk_seconds = (
        container.settings.monthly_talk_minutes * 60
    )
    r = client.post("/api/conversations", json={"mission_id": m["id"]})
    assert r.status_code == 402
    err = r.json()["error"]
    assert err["reason"] == "allowance" and "renews on" in err["message"]
    assert client.get("/api/billing").json()["status"] == "exhausted"


def _hook(client, event, entity, secret=HOOK):
    raw = json.dumps({"event": event, "payload": {"subscription": {"entity": entity}}}).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/billing/webhook",
        content=raw,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )


def test_webhook_checks_its_signature_and_renews(client, rzp, container):
    sid, _ = _subscribe(client, rzp)
    pid = container.settings.default_principal_id
    assert _hook(client, "subscription.charged", rzp.subs[sid], secret="nope").status_code == 401

    # Renewal: a new period starts, so earlier talk time no longer counts.
    later = int(time.time()) + 31 * 86400
    renewed = rzp.subs[sid] | {"current_start": later - 86400, "current_end": later + 29 * 86400}
    assert _hook(client, "subscription.charged", renewed).status_code == 200
    sub = container.repo.subscriptions[pid]
    assert int(sub.current_start.timestamp()) == later - 86400


def test_cancelled_keeps_access_until_the_period_ends(client, rzp, container):
    sid, _ = _subscribe(client, rzp)
    status = client.post("/api/billing/cancel").json()
    assert (
        rzp.cancelled == [sid] and status["cancel_at_period_end"] and status["status"] == "active"
    )
    pid = container.settings.default_principal_id
    sub = container.repo.subscriptions[pid]
    container.repo.subscriptions[pid] = sub.model_copy(
        update={"status": "cancelled", "current_end": utcnow() - timedelta(minutes=1)}
    )
    assert client.get("/api/billing").json()["status"] == "trial"


def test_subscription_access_rules():
    now = utcnow()
    sub = Subscription(
        principal_id="p",
        razorpay_subscription_id="s",
        status="active",
        current_end=now + timedelta(days=1),
    )
    assert sub.grants_access(now)
    assert sub.model_copy(update={"status": "halted"}).grants_access(now)
    assert not sub.model_copy(update={"status": "created"}).grants_access(now)
    assert not sub.grants_access(now + timedelta(days=2))


# ---- the meter inside a live call ---------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def test_only_live_states_are_metered(make_session, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(session_mod.time, "monotonic", clock)
    s = make_session(ScriptedLLM({"turn": []}))
    s._meter(S.listening)
    clock.t += 30
    s._meter(S.paused)
    clock.t += 300  # paused: not counted
    s._meter(S.awaiting_approval)
    clock.t += 60  # waiting on you: not counted
    s._meter(S.speaking)
    clock.t += 15
    assert round(s.talk_used()) == 45
    s._meter(S.completed)
    assert s.conv.talk_seconds == 45


async def test_running_out_mid_call_ends_with_a_goodbye(make_session):
    llm = ScriptedLLM(
        {"turn": [turn("I'm sorry, I need to stop here. Ananya will follow up.")], "report": []}
    )
    s = make_session(llm)
    await s.start()
    await settle(s)
    s.allowance, s.out_of_time = 0.0, True
    await s.counterparty_said("₹34,000.", "English", "text")
    await settle(s)
    assert s.state == S.completed
    agent = [e.text for e in s.events if e.speaker.value == "agent"]
    assert "follow up" in agent[-1]
    assert s.events[-1].speaker.value == "system" and "Talk time used up" in s.events[-1].text
    assert "Talk time is up" in llm.last_prompt("turn")


async def test_start_is_refused_without_talk_time(make_session, rzp):
    s = make_session(ScriptedLLM({"turn": [turn("hello")]}))
    sent = []

    async def sink(msg):
        sent.append(msg)

    s.attach(sink)
    await s.start()  # not the trial mission, no subscription
    assert s.state == S.ready
    err = next(m for m in sent if m["type"] == "error")["error"]
    assert err["kind"] == "payment_required"


def test_trial_is_three_minutes_in_total(client, container):
    assert container.settings.trial_talk_minutes == 3
    m = _mission(client).json()
    _ready(client, m["id"])
    first = client.post("/api/conversations", json={"mission_id": m["id"]}).json()
    container.repo.conversations[first["id"]].talk_seconds = 100
    second = client.post("/api/conversations", json={"mission_id": m["id"]})
    assert second.status_code == 200  # 80 s left across calls
    container.repo.conversations[second.json()["id"]].talk_seconds = 80
    r = client.post("/api/conversations", json={"mission_id": m["id"]})
    assert r.status_code == 402 and r.json()["error"]["reason"] == "subscribe"


def test_short_allowances_get_a_short_grace():
    assert session_mod.overrun_grace(180) < 30
    assert session_mod.overrun_grace(180) >= 15
    assert session_mod.overrun_grace(180 * 60) == 45


async def test_trial_running_out_ends_the_call_and_asks_to_subscribe(make_session, rzp):
    llm = ScriptedLLM(
        {"turn": [turn("I'm sorry, I need to stop here. Ananya will follow up.")], "report": []}
    )
    s = make_session(llm)
    s.allowance = None  # start without billing checks, then run out mid-call
    sent = []

    async def sink(msg):
        sent.append(msg)

    s.attach(sink)
    s._has_time = _always_true
    await s.start()
    await settle(s)
    s.allowance, s.out_of_time = 0.0, True
    await s.counterparty_said("₹34,000.", "English", "text")
    await settle(s)
    assert s.state == S.completed
    errors = [m["error"] for m in sent if m["type"] == "error"]
    assert any(e["kind"] == "payment_required" and e.get("reason") == "subscribe" for e in errors)


async def _always_true():
    return True


def test_free_accounts_never_pay_or_run_out(client, container, monkeypatch):
    from app.core import auth

    pid = container.settings.default_principal_id
    container.settings.free_accounts = "Owner@Example.com, other@example.com"
    monkeypatch.setitem(auth._verified_emails, pid, "owner@example.com")
    assert client.get("/api/billing").json() == client.get("/api/billing").json() | {
        "enabled": False, "status": "free"}
    first = _mission(client).json()
    for _ in range(3):  # no trial: every mission is allowed
        assert _mission(client).status_code == 200
    _ready(client, first["id"])
    conv = client.post("/api/conversations", json={"mission_id": first["id"]}).json()
    container.repo.conversations[conv["id"]].talk_seconds = 10_000  # far past any cap
    assert client.post("/api/conversations", json={"mission_id": first["id"]}).status_code == 200


def test_a_profile_email_does_not_make_an_account_free(client, container, monkeypatch):
    from app.core import auth

    pid = container.settings.default_principal_id
    container.settings.free_accounts = "owner@example.com"
    monkeypatch.setitem(auth._verified_emails, pid, "someone@else.com")
    _mission(client)
    assert _mission(client).status_code == 402  # trial used, still has to subscribe
