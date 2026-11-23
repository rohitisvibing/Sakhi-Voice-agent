"""The phone line (Exotel two-way stream) as a counterparty channel.

Audio in: base64 PCM16 `media` events -> the session's speech-to-text, like the browser mic.
Audio out: Sakhi's speech (and keypad tones) as `media` events in 100 ms chunks, followed by a
`mark`; Exotel echoes the mark when that audio has finished playing on the call. `clear` drops
queued audio when the other person starts talking over her.
"""

import asyncio
import base64
import contextlib
import logging
import time
from array import array
from typing import TYPE_CHECKING

from app.models.conversation import ConversationState
from app.providers.tts.base import Voice

from . import dtmf
from .counterparty import CounterpartyChannel

if TYPE_CHECKING:
    from app.models.transcript import TranscriptEvent

    from .session import LiveSession

log = logging.getLogger(__name__)

CHECK_IN_DIRECTIVE = ("The other person has gone quiet. In one short sentence, check they are "
                      "still on the line.")
GOODBYE_DIRECTIVE = ("The other person is still silent. Say a short, polite goodbye, say the "
                     "principal will try again later, and set end_conversation=true.")


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Linear resampling of mono PCM16 (only 8k <-> 16k is needed here)."""
    if src == dst or not pcm:
        return pcm
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    n_out = int(len(a) * dst / src)
    out = array("h", bytes(2 * n_out))
    step = src / dst
    last = len(a) - 1
    for i in range(n_out):
        x = i * step
        j = int(x)
        f = x - j
        b = a[j + 1] if j < last else a[last]
        out[i] = int(a[j] + (b - a[j]) * f)
    return out.tobytes()


class PhoneChannel(CounterpartyChannel):
    kind = "phone"
    uses_microphone = True
    handles_audio = True
    HELLO_WAIT_S = 3.0      # silence after answering: open anyway
    GREETING_MAX_S = 15.0   # they started talking (a long menu?): let them finish first
    SILENCE_S = 8.0
    STT_RATE = 16000

    def __init__(self) -> None:
        self.ws = None
        self.stream_sid: str | None = None
        self.rate = 16000
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._marks: dict[str, asyncio.Event] = {}
        self._hello = asyncio.Event()
        self._greeted = asyncio.Event()
        self._silence: asyncio.Task | None = None
        self.silent_checks = 0
        self.marks_sent = 0

    def bind(self, ws, stream_sid: str | None, rate: int) -> None:
        self.ws, self.stream_sid, self.rate = ws, stream_sid, rate or 16000

    # ---- sending ---------------------------------------------------------------------------
    async def _send(self, msg: dict) -> None:
        if self.ws is None or self.closed:
            return
        async with self._send_lock:
            await self.ws.send_json(msg)

    @property
    def chunk_bytes(self) -> int:
        return self.rate // 10 * 2  # 100 ms of PCM16: always a multiple of 320 bytes

    async def _send_audio(self, pcm: bytes) -> None:
        await self._send({"event": "media", "stream_sid": self.stream_sid,
                          "media": {"payload": base64.b64encode(pcm).decode()}})

    async def _stream(self, chunks, src_rate: int) -> float:
        """Send audio in 100 ms frames; returns its duration in seconds."""
        buf, total = bytearray(), 0
        size = self.chunk_bytes
        async for pcm in chunks:
            pcm = resample(pcm, src_rate, self.rate)
            buf += pcm
            total += len(pcm)
            while len(buf) >= size:
                await self._send_audio(bytes(buf[:size]))
                del buf[:size]
        if buf:
            buf += b"\x00" * (size - len(buf))  # pad the tail to a whole frame
            await self._send_audio(bytes(buf))
        return total / 2 / self.rate

    async def _played(self, name: str, duration: float, started: float) -> None:
        """Wait until Exotel says this audio has been heard (or it reasonably should have)."""
        ev = asyncio.Event()
        self._marks[name] = ev
        self.marks_sent += 1
        await self._send({"event": "mark", "stream_sid": self.stream_sid,
                          "mark": {"name": name}})
        remaining = max(0.5, duration - (time.monotonic() - started))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ev.wait(), timeout=remaining + 4)
        self._marks.pop(name, None)

    async def play(self, session: "LiveSession", event: "TranscriptEvent", voice: Voice) -> None:
        tts = session.c.phone_tts or session.c.tts
        started = time.monotonic()
        try:
            duration = await self._stream(tts.synthesize(event.text, voice), tts.sample_rate)
            await self._played(event.id, duration, started)
        except asyncio.CancelledError:
            await self.clear()
            raise

    async def press(self, session: "LiveSession", digits: str) -> None:
        """Play keypad tones into the call."""
        pause = session.c.settings.ivr_pause_ms
        pcm = dtmf.sequence(digits, self.rate, pause_ms=pause)

        async def once():
            yield pcm

        started = time.monotonic()
        duration = await self._stream(once(), self.rate)
        await self._played(f"dtmf-{time.time_ns()}", duration, started)

    async def clear(self) -> None:
        with contextlib.suppress(Exception):
            await self._send({"event": "clear", "stream_sid": self.stream_sid})

    VOICE_RMS = 700  # PCM16 level that counts as someone talking on the line

    def on_inbound(self, pcm: bytes) -> None:
        """Their audio: notice speech before any transcript arrives (for the greeting)."""
        if self._hello.is_set() or len(pcm) < 2:
            return
        a = array("h")
        a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        step = max(1, len(a) // 200)
        sample = a[::step]
        rms = (sum(x * x for x in sample) / len(sample)) ** 0.5
        if rms > self.VOICE_RMS:
            self._hello.set()

    def on_mark(self, name: str) -> None:
        ev = self._marks.get(name)
        if ev:
            ev.set()

    # ---- the conversation ------------------------------------------------------------------
    async def prepare(self, session: "LiveSession") -> None:
        """Let them say "Hello?" first (or a moment of silence) before Sakhi opens. If they
        start talking, wait for them to finish: a menu may be reading out its options."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._hello.wait(), timeout=self.HELLO_WAIT_S)
        if self._hello.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._greeted.wait(), timeout=self.GREETING_MAX_S)

    def heard_speech(self, session: "LiveSession", final: bool = False) -> None:
        self._hello.set()
        if final:
            self._greeted.set()
        self.silent_checks = 0
        if self._silence and not self._silence.done() \
                and self._silence is not asyncio.current_task():
            self._silence.cancel()
        self._silence = None

    async def after_agent_turn(self, session: "LiveSession") -> None:
        await session.set_state_listening()
        self._arm_silence(session)

    def _arm_silence(self, session: "LiveSession") -> None:
        if self._silence and not self._silence.done():
            self._silence.cancel()
        self._silence = asyncio.create_task(self._watch_silence(session))

    async def _watch_silence(self, session: "LiveSession") -> None:
        await asyncio.sleep(self.SILENCE_S)
        busy = session.task is not None and not session.task.done()
        if session.state != ConversationState.listening or busy or session.paused \
                or session.takeover_active:
            return
        self.silent_checks += 1
        directive = CHECK_IN_DIRECTIVE if self.silent_checks == 1 else GOODBYE_DIRECTIVE
        session.schedule(session.run_agent_turn(directive=directive), kind="agent")

    async def close(self, session: "LiveSession") -> None:
        """Hang up: Exotel ends the call when the stream socket closes."""
        if self._silence and not self._silence.done() \
                and self._silence is not asyncio.current_task():
            self._silence.cancel()
        if self.closed:
            return
        self.closed = True
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
