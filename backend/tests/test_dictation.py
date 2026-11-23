"""Spoken requests: a recording becomes text the person reviews before building a mission."""

import io
import wave

from fastapi.testclient import TestClient

from app.main import app

from .fakes import FakeSTT


def _wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def _post(client, data: bytes):
    return client.post("/api/transcribe", files={"audio": ("request.wav", data, "audio/wav")})


def test_a_recording_comes_back_as_text(container):
    container.settings.sarvam_api_key = "test"
    container.stt = FakeSTT("Call Rahul and negotiate the rent down to 26,000.", "hi-IN")
    r = _post(TestClient(app), _wav(2))
    assert r.status_code == 200
    assert r.json() == {"text": "Call Rahul and negotiate the rent down to 26,000.",
                        "language": "Hindi"}
    assert container.stt.uploads  # the audio reached speech-to-text


def test_too_short_or_too_long_recordings_are_refused(container):
    container.settings.sarvam_api_key = "test"
    container.stt = FakeSTT()
    client = TestClient(app)
    assert _post(client, _wav(0.2)).status_code == 409
    assert _post(client, _wav(31)).status_code == 409
    assert not container.stt.uploads


def test_without_speech_keys_it_says_so(container):
    r = _post(TestClient(app), _wav(2))
    assert r.status_code >= 400 and "isn't set up" in r.json()["error"]["message"]
