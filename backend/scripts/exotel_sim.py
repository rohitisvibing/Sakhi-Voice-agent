"""Play the person Sakhi calls, speaking Exotel's stream protocol to a local backend.

    EXOTEL_SIMULATE=true TELEPHONY_SECRET=dev uv run uvicorn app.main:app --port 8000
    # place a phone call in the app; the backend logs "SIMULATED CALL ... stream_url=wss://..."
    uv run python scripts/exotel_sim.py "<stream_url>" [--ivr] [--out call.wav]

The callee's lines are spoken with Sarvam TTS (a male voice) and streamed in real time in
100 ms frames. Sakhi's audio is written to --out, marks are echoed once her audio would have
finished playing, `clear` drops what is queued, and keypad tones she sends are decoded.
"""

import argparse
import asyncio
import base64
import json
import math
import sys
import time
import wave
from array import array
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.providers.tts.base import Voice
from app.providers.tts.sarvam import SarvamTTS

RATE = 16000
FRAME = RATE // 10 * 2
LINES = [
    "Hello, Rahul speaking. Who is this?",
    "Okay. Thirty five thousand is already a fair rent for this area, you know.",
    "I can maybe do thirty three thousand, not less. Maintenance has also gone up.",
    "Fine, thirty two thousand if she signs for twelve months. That's my final offer.",
    "Okay, let me know by tomorrow. Bye.",
]
IVR = ("Welcome to Sakhi Properties. For English, press 1. Hindi ke liye, do dabaayein. "
       "To speak to the owner, press 9.")
ROWS, COLS = [697, 770, 852, 941], [1209, 1336, 1477, 1633]
KEYS = ["123A", "456B", "789C", "*0#D"]


def goertzel(samples, freq):
    k = 2 * math.cos(2 * math.pi * freq / RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def detect_digit(pcm: bytes) -> str | None:
    a = array("h")
    a.frombytes(pcm)
    if not a or max(abs(x) for x in a) < 2000:
        return None
    r = [goertzel(a, f) for f in ROWS]
    c = [goertzel(a, f) for f in COLS]
    ri, ci = max(range(4), key=r.__getitem__), max(range(4), key=c.__getitem__)
    # A pure tone pair holds nearly all of the frame's energy; speech never does.
    total = sum(x * x for x in a) * len(a) / 2
    if total and (r[ri] + c[ci]) / total > 0.4 and min(r[ri], c[ci]) > 0.2 * max(r[ri], c[ci]):
        return KEYS[ri][ci]
    return None


class Callee:
    def __init__(self, ws, out: Path) -> None:
        self.ws, self.out = ws, out
        self.heard = bytearray()
        self.playing_until = time.monotonic()
        self.last_audio = 0.0
        self.digits: list[str] = []
        self._prev_digit: str | None = None
        self.got_audio = asyncio.Event()

    async def receive(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            ev = msg.get("event")
            now = time.monotonic()
            if ev == "media":
                pcm = base64.b64decode(msg["media"]["payload"])
                self.heard += pcm
                self.playing_until = max(self.playing_until, now) + len(pcm) / 2 / RATE
                self.last_audio = now
                self.got_audio.set()
                d = detect_digit(pcm)
                if d and d != self._prev_digit:  # one press can span two frames
                    self.digits.append(d)
                    print(f"   [keypad] Sakhi pressed {d}")
                self._prev_digit = d
            elif ev == "mark":
                delay = max(0.0, self.playing_until - now)
                asyncio.get_running_loop().call_later(delay, asyncio.ensure_future,
                                                      self._echo(msg["mark"]["name"]))
            elif ev == "clear":
                print("   [clear] Sakhi stopped talking (barged in)")
                self.playing_until = now

    async def _echo(self, name: str) -> None:
        try:
            await self.ws.send(json.dumps({"event": "mark", "mark": {"name": name}}))
        except websockets.ConnectionClosed:
            pass

    async def speak(self, clips: dict[str, bytes], text: str) -> None:
        print(f"Rahul: {text}")
        pcm = clips[text] + b"\x00\x00" * (RATE // 2)
        for i in range(0, len(pcm), FRAME):
            frame = pcm[i:i + FRAME].ljust(FRAME, b"\x00")
            await self.ws.send(json.dumps({"event": "media", "media": {
                "payload": base64.b64encode(frame).decode()}}))
            await asyncio.sleep(0.1)

    async def silence(self, seconds: float) -> None:
        frame = base64.b64encode(b"\x00" * FRAME).decode()
        for _ in range(int(seconds * 10)):
            await self.ws.send(json.dumps({"event": "media", "media": {"payload": frame}}))
            await asyncio.sleep(0.1)

    async def until_sakhi_done(self, timeout: float = 25.0) -> None:
        """Keep the line open (silence) until Sakhi has spoken and finished."""
        self.got_audio.clear()
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            await self.silence(0.3)
            now = time.monotonic()
            if self.got_audio.is_set() and now > self.playing_until + 0.8 \
                    and now - self.last_audio > 1.0:
                return


async def render(lines: list[str]) -> dict[str, bytes]:
    """Synthesise every line up front, so the callee answers without TTS delays."""
    s = get_settings().model_copy(update={"sarvam_tts_sample_rate": RATE})
    tts = SarvamTTS(s)
    voice = Voice(speaker="rahul", language_code="en-IN")
    clips = {}
    for text in lines:
        clips[text] = b"".join([c async for c in tts.synthesize(text, voice)])
    return clips


async def main(url: str, ivr: bool, out: Path) -> None:
    tts = await render([IVR, "Hello?", *LINES])
    url = url.replace("wss://www.sakhi.buzz", "ws://localhost:8000")
    async with websockets.connect(url, max_size=None) as ws:
        callee = Callee(ws, out)
        rx = asyncio.create_task(callee.receive())
        await ws.send(json.dumps({"event": "connected"}))
        await ws.send(json.dumps({"event": "start", "stream_sid": "sim_stream", "start": {
            "call_sid": "sim_call", "media_format": {"encoding": "slin", "sample_rate": RATE}}}))
        await callee.silence(0.5)
        if ivr:
            print("[menu]")
            await callee.speak(tts, IVR)
            for _ in range(80):
                await callee.silence(0.25)
                if callee.digits:
                    break
            print(f"[menu] got {''.join(callee.digits) or 'nothing'}; connecting to Rahul")
            await callee.silence(1.0)
        await callee.speak(tts, "Hello?")
        await callee.until_sakhi_done()
        for line in LINES:
            await callee.speak(tts, line)
            await callee.until_sakhi_done()
        await ws.send(json.dumps({"event": "stop", "stop": {"reason": "callended"}}))
        rx.cancel()
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(callee.heard))
    print(f"Sakhi's side of the call: {out} ({len(callee.heard) / 2 / RATE:.1f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--ivr", action="store_true")
    ap.add_argument("--out", default="call.wav")
    a = ap.parse_args()
    asyncio.run(main(a.url, a.ivr, Path(a.out)))
