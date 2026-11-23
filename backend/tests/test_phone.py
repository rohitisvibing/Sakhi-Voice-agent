"""Phone calls through Exotel: placing the call, the stream bridge, and the call's lifecycle."""

import asyncio
import base64
import time

import pytest
from fastapi.testclient import TestClient

from app.core import telephony_tokens as tokens
from app.main import app
from app.models.conversation import CallStatus, ConversationMode, normalize_phone
from app.models.conversation import ConversationState as S
from app.realtime import phone as phone_mod
from app.realtime.phone import PhoneChannel, resample

from .conftest import RENT_REQUEST
from .fakes import FakeExotel, FakeSTT, FakeTTS, ScriptedLLM
from .test_conversation_flow import settle, turn

SECRET = "telephony-test-secret"


class FakeCallSocket:
    """Exotel's side of the stream: records what Sakhi sends, echoes marks when asked."""

    def __init__(self, channel: PhoneChannel | None = None, *, echo_marks=True):
        self.sent: list[dict] = []
        self.closed = False
        self.channel = channel
        self.echo_marks = echo_marks

    async def send_json(self, msg):
        self.sent.append(msg)
        if msg["event"] == "mark" and self.echo_marks and self.channel:
            name = msg["mark"]["name"]
            asyncio.get_running_loop().call_later(0.01, self.channel.on_mark, name)

    async def close(self):
        self.closed = True

    def events(self, kind):
        return [m for m in self.sent if m["event"] == kind]


@pytest.fixture
def phone_settings(container):
    s = container.settings
    s.telephony_secret = SECRET
    s.exotel_simulate = True
    s.sarvam_api_key = "test"
    container.telephony = FakeExotel()
    container.stt = FakeSTT()
    container.tts = FakeTTS(22050)
    container.phone_tts = FakeTTS(16000)
    return s


def phone_session(make_session, container, llm):
    s = make_session(llm, mode=ConversationMode.phone)
    s.c.stt, s.c.tts, s.c.phone_tts = container.stt, container.tts, container.phone_tts
    s.conv.recipient_name, s.conv.recipient_phone = "Rahul", "+919876543210"
    s.voice_enabled = True
    ws = FakeCallSocket(s.channel)
    s.channel.bind(ws, "stream_1", 16000)
    return s, ws


async def hear(s, text):
    """The callee says something: audio opens the speech-to-text stream, then a transcript."""
    await s.on_audio(b"\x00\x00" * 1600)
    s.c.stt.streams[-1].say(text)
    await asyncio.sleep(0.05)


# ---- numbers, tokens ----------------------------------------------------------------------
def test_phone_numbers_are_normalised():
    assert normalize_phone("98765 43210") == "+919876543210"
    assert normalize_phone("098765-43210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    for bad in ("12345", "+91 12345 67890", "hello"):
        with pytest.raises(ValueError):
            normalize_phone(bad)


def test_tokens_are_signed_scoped_and_single_use():
    t = tokens.make(SECRET, "conv1", tokens.STREAM, 60)
    assert tokens.check(SECRET, t, tokens.STATUS) is None  # wrong purpose
    assert tokens.check("other", t, tokens.STREAM) is None  # wrong key
    tampered = t[:-1] + ("1" if t[-1] == "0" else "0")
    assert tokens.check(SECRET, tampered, tokens.STREAM) is None
    assert tokens.check(SECRET, t, tokens.STREAM, single_use=True) == "conv1"
    assert tokens.check(SECRET, t, tokens.STREAM, single_use=True) is None  # replayed
    old = tokens.make(SECRET, "conv1", tokens.STATUS, -1)
    assert tokens.check(SECRET, old, tokens.STATUS) is None  # expired


def test_resampling_keeps_duration():
    pcm = b"\x10\x00" * 800  # 0.1 s at 8 kHz
    assert len(resample(pcm, 8000, 16000)) == 3200
    assert len(resample(resample(pcm, 8000, 16000), 16000, 8000)) == 1600


# ---- placing the call ---------------------------------------------------------------------
def _prepared_mission(client):
    m = client.post("/api/missions/extract", json={"text": RENT_REQUEST}).json()
    client.post(f"/api/missions/{m['id']}/approve")
    client.post(f"/api/missions/{m['id']}/prepare")
    return m


def test_a_call_needs_a_recipient_and_an_explicit_confirmation(phone_settings, container):
    client = TestClient(app)
    m = _prepared_mission(client)
    r = client.post("/api/conversations", json={"mission_id": m["id"], "mode": "phone"})
    assert r.status_code == 409 and "Choose who" in r.json()["error"]["message"]
    r = client.post("/api/conversations", json={
        "mission_id": m["id"], "mode": "phone",
        "recipient": {"name": "Rahul", "phone": "98765 43210"}})
    conv = r.json()
    assert conv["recipient_phone"] == "+919876543210" and conv["call_status"] == "preparing"

    assert client.post(f"/api/conversations/{conv['id']}/dial", json={}).status_code == 422
    assert not container.telephony.calls  # nothing dialled without confirm
    r = client.post(f"/api/conversations/{conv['id']}/dial", json={"confirm": True})
    assert r.status_code == 200 and r.json()["call_status"] == "initiating"
    call = container.telephony.calls[0]
    assert call["to"] == "+919876543210"
    assert call["stream_url"].startswith("wss://www.sakhi.buzz/ws/telephony/exotel?")
    assert "sample-rate=16000" in call["stream_url"]
    t = call["stream_url"].split("t=")[1]
    assert tokens.check(SECRET, t, tokens.STREAM) == conv["id"]
    # One call at a time.
    assert client.post(f"/api/conversations/{conv['id']}/dial",
                       json={"confirm": True}).status_code == 409


def test_phone_calls_are_refused_when_not_set_up(container):
    client = TestClient(app)
    m = _prepared_mission(client)
    r = client.post("/api/conversations", json={
        "mission_id": m["id"], "mode": "phone",
        "recipient": {"name": "Rahul", "phone": "9876543210"}})
    assert r.status_code == 503 and "aren't set up" in r.json()["error"]["message"]


def test_status_callbacks_are_verified_and_only_move_forward(phone_settings, container):
    client = TestClient(app)
    m = _prepared_mission(client)
    conv = client.post("/api/conversations", json={
        "mission_id": m["id"], "mode": "phone",
        "recipient": {"name": "Rahul", "phone": "9876543210"}}).json()
    client.post(f"/api/conversations/{conv['id']}/dial", json={"confirm": True})
    status_url = container.telephony.calls[0]["status_url"]
    path = status_url.split("sakhi.buzz")[1]
    assert client.post("/api/telephony/exotel/status?t=forged",
                       json={"Status": "busy"}).status_code == 401
    assert client.post(path, json={"Status": "ringing", "CallSid": "call_1"}).status_code == 200
    assert container.repo.conversations[conv["id"]].call_status == CallStatus.ringing
    client.post(path, json={"Status": "no-answer", "CallSid": "call_1"})
    client.post(path, json={"Status": "ringing", "CallSid": "call_1"})  # late duplicate
    stored = container.repo.conversations[conv["id"]]
    assert stored.call_status == CallStatus.no_answer
    notes = [e.text for e in container.repo.events[conv["id"]]]
    assert "Rahul didn't answer." in notes
    # They can try again.
    r = client.post(f"/api/conversations/{conv['id']}/dial", json={"confirm": True})
    assert r.status_code == 200 and len(container.telephony.calls) == 2
    recent = client.get("/api/recipients/recent").json()
    assert recent == [{"name": "Rahul", "phone": "+919876543210"}]


# ---- the call itself ----------------------------------------------------------------------
async def test_sakhi_waits_for_hello_then_speaks_in_100ms_frames(make_session, container,
                                                                 phone_settings):
    llm = ScriptedLLM({"turn": [turn("Could we talk about the rent?")]})
    s, ws = phone_session(make_session, container, llm)
    await s.start_phone()
    await asyncio.sleep(0.05)
    assert container.phone_tts.lines == []  # still waiting for them to pick up and speak
    await hear(s, "Hello?")
    await settle(s)
    opening = s.mission.brief.opening_statement
    assert container.phone_tts.lines[0] == opening
    frames = ws.events("media")
    assert frames and all(len(base64.b64decode(f["media"]["payload"])) == 3200 for f in frames)
    assert ws.events("mark")[0]["mark"]["name"] == next(
        e.id for e in s.events if e.text == opening)
    assert s.conv.call_status == CallStatus.in_conversation
    assert s.state == S.listening


async def test_talking_over_sakhi_clears_her_queued_audio(make_session, container,
                                                          phone_settings):
    llm = ScriptedLLM({"turn": [turn("Sure.")]})
    s, ws = phone_session(make_session, container, llm)
    ws.echo_marks = False  # her line is still "playing"
    await s.start_phone()
    await hear(s, "Hello?")
    await asyncio.sleep(0.1)
    assert s.state == S.speaking
    stream = container.stt.streams[-1]
    stream.partial("Wait, wait")
    await asyncio.sleep(0.1)
    assert ws.events("clear")
    assert s.state in (S.listening, S.thinking, S.speaking)


async def test_silence_gets_a_check_in_then_a_goodbye(make_session, container, phone_settings,
                                                      monkeypatch):
    monkeypatch.setattr(PhoneChannel, "SILENCE_S", 0.05)
    llm = ScriptedLLM({"turn": [turn("Are you still there?"),
                                turn("I'll try again later. Goodbye.", end_conversation=True)],
                       "report": []})
    s, ws = phone_session(make_session, container, llm)
    await s.start_phone()
    await hear(s, "Hello?")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if s.state == S.completed:
            break
    assert s.state == S.completed
    prompts = [m for t, m in llm.calls if t == "turn"]
    assert len(prompts) == 2
    assert "check they are still on the line" in llm.last_prompt("turn") or any(
        "still on the line" in msg.content for msgs in prompts for msg in msgs)
    assert ws.closed  # hung up
    assert s.conv.call_status == CallStatus.completed


async def test_the_call_limit_wraps_up_politely(make_session, container, phone_settings):
    container.settings.phone_call_limit_seconds = 100
    s, _ = phone_session(make_session, container, ScriptedLLM({"turn": []}))
    await s.refresh_allowance()
    assert s.allowance == pytest.approx(100, abs=1)
    assert s.call_capped


async def test_keypad_tones_are_sent_into_the_call(make_session, container, phone_settings):
    s, ws = phone_session(make_session, container, ScriptedLLM({"turn": []}))
    await s.channel.press(s, "1,2")
    pcm = b"".join(base64.b64decode(f["media"]["payload"]) for f in ws.events("media"))
    assert len(pcm) > 16000 * 2 * 0.6  # two tones, gaps and a pause
    assert ws.events("mark")


def test_exotel_stream_end_to_end(phone_settings, container):
    """Exotel's socket: start (with our token), media, marks, and the callee hanging up."""
    client = TestClient(app)
    m = _prepared_mission(client)
    conv = client.post("/api/conversations", json={
        "mission_id": m["id"], "mode": "phone",
        "recipient": {"name": "Rahul", "phone": "9876543210"}}).json()
    client.post(f"/api/conversations/{conv['id']}/dial", json={"confirm": True})
    url = container.telephony.calls[0]["stream_url"].split("sakhi.buzz")[1]
    silence = base64.b64encode(b"\x00\x00" * 1600).decode()
    with client.websocket_connect(url.replace("wss://", "")) as ws:
        ws.send_json({"event": "connected"})
        ws.send_json({"event": "start", "stream_sid": "s1",
                      "start": {"call_sid": "call_1", "media_format": {"sample_rate": 16000}}})
        ws.send_json({"event": "media", "media": {"payload": silence}})
        time.sleep(0.2)
        container.stt.streams[-1].say("Hello?")
        got = ws.receive_json()
        while got["event"] != "mark":
            assert got["event"] == "media"
            got = ws.receive_json()
        ws.send_json({"event": "mark", "mark": {"name": got["mark"]["name"]}})
        ws.send_json({"event": "stop", "stop": {"reason": "callended"}})
    stored = container.repo.conversations[conv["id"]]
    assert stored.call_status == CallStatus.completed
    texts = [e.text for e in container.repo.events[conv["id"]]]
    assert "Rahul hung up." in texts
    assert container.phone_tts.lines  # the opening was spoken into the call
    report = container.repo.reports[conv["id"]]
    assert report.call.recipient_name == "Rahul" and report.call.recipient_phone == "+919876543210"


def test_a_forged_stream_is_refused(phone_settings, container):
    client = TestClient(app)
    with client.websocket_connect("/ws/telephony/exotel?t=forged") as ws:
        ws.send_json({"event": "start", "stream_sid": "s1", "start": {}})
        with pytest.raises(Exception):  # noqa: B017 - the server closes the socket
            ws.receive_json()


def test_module_constants():
    assert phone_mod.CHECK_IN_DIRECTIVE and phone_mod.GOODBYE_DIRECTIVE


# ---- phone menus ------------------------------------------------------------------------
async def test_a_phone_menu_gets_key_presses_not_words(make_session, container, phone_settings):
    llm = ScriptedLLM({"turn": [
        turn("", hold=True),  # still listing options
        turn("", dtmf="2", dtmf_label="Hindi"),
    ]})
    s, _ws = phone_session(make_session, container, llm)
    s.mission.brief.opening_statement = "Hello, this is Sakhi, calling for Ananya."
    await s.start_phone()
    await hear(s, "Welcome to the helpline. Press 1 for English.")
    await settle(s)
    spoken = len(container.phone_tts.lines)
    s.c.stt.streams[-1].say("Press 2 for Hindi.")
    await asyncio.sleep(0.05)
    await settle(s)
    assert len(container.phone_tts.lines) == spoken  # nothing said to the menu
    assert s.conv.call_status == CallStatus.navigating_ivr
    assert s.negotiation.ivr_path == ["2: Hindi"]
    assert any(e.text == "Pressed 2 (Hindi)" for e in s.events)
    assert "CHANNEL: a phone call" in llm.last_prompt("turn")


async def test_an_unclear_menu_asks_the_principal(make_session, container, phone_settings):
    need = {"kind": "ivr_choice", "request": "1 sales, 2 support, 3 accounts",
            "proposed": "Press 3 for accounts", "reason": "Not sure which handles rent"}
    llm = ScriptedLLM({"turn": [turn("", dtmf="3", needs_approval=need)]})
    s, _ws = phone_session(make_session, container, llm)
    s.mission.brief.opening_statement = "Hello, this is Sakhi."
    await s.start_phone()
    await hear(s, "Press 1 for sales, 2 for support, 3 for accounts.")
    await settle(s)
    assert s.state == S.awaiting_approval
    approval = next(iter(s.approvals.values()))
    assert approval.kind.value == "ivr_choice"
    spoken_before = len(container.phone_tts.lines)
    from app.models.conversation import ApprovalDecision, ApprovalStatus
    await s.decide(approval.id, ApprovalDecision(decision=ApprovalStatus.approved))
    await settle(s)
    assert len(container.phone_tts.lines) == spoken_before  # no "one moment" to a machine
    assert s.negotiation.ivr_path and s.negotiation.ivr_path[-1].startswith("3")


def _power(samples, rate, freq):
    """Goertzel: how much of `freq` is in the samples."""
    import math
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def test_keypad_tones_are_the_standard_frequency_pairs():
    from array import array

    from app.realtime import dtmf
    rate = 16000
    for digit, (lo, hi) in {"1": (697, 1209), "5": (770, 1336), "#": (941, 1477)}.items():
        a = array("h")
        a.frombytes(dtmf.tone(digit, rate))
        freqs = [697, 770, 852, 941, 1209, 1336, 1477, 1633]
        powers = {f: _power(a, rate, f) for f in freqs}
        top_two = sorted(powers, key=powers.get, reverse=True)[:2]
        assert sorted(top_two) == [lo, hi], digit
    assert dtmf.clean("1-2 3#x,*") == "123#,*"


async def test_a_person_saying_hello_gets_words_not_keys(make_session, container,
                                                          phone_settings):
    llm = ScriptedLLM({"turn": [turn("", dtmf="1")]})
    s, _ws = phone_session(make_session, container, llm)
    await s.start_phone()
    await hear(s, "Press 1 for English.")  # the menu answers first
    await settle(s)
    llm.push("turn", turn("", dtmf="9"), turn("Hi Rahul, this is Sakhi, calling for Ananya."))
    s.c.stt.streams[-1].say("Hello?")
    await asyncio.sleep(0.05)
    await settle(s)
    assert container.phone_tts.lines[-1] == "Hi Rahul, this is Sakhi, calling for Ananya."
    assert "a person is on the line" in llm.last_prompt("turn")


def test_ending_the_call_from_another_server_hangs_up(phone_settings, container):
    """The browser may reach a different server: its End reaches the call via the database."""
    client = TestClient(app)
    m = _prepared_mission(client)
    conv = client.post("/api/conversations", json={
        "mission_id": m["id"], "mode": "phone",
        "recipient": {"name": "Rahul", "phone": "9876543210"}}).json()
    client.post(f"/api/conversations/{conv['id']}/dial", json={"confirm": True})
    url = container.telephony.calls[0]["stream_url"].split("sakhi.buzz")[1]
    with client.websocket_connect(url) as ws:
        ws.send_json({"event": "start", "stream_sid": "s1", "start": {}})
        time.sleep(0.2)
        container.repo.call_commands.setdefault(conv["id"], []).append(
            {"type": "end", "payload": {}})
        closed = False
        for _ in range(200):  # Sakhi's audio, then the socket closes (the hang-up)
            try:
                ws.receive_json()
            except Exception:  # noqa: BLE001 - WebSocketDisconnect
                closed = True
                break
        assert closed
    stored = container.repo.conversations[conv["id"]]
    assert stored.call_status == CallStatus.completed
    assert "You ended the call." in [e.text for e in container.repo.events[conv["id"]]]
