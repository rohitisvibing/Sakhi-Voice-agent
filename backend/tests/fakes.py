"""Scripted providers for deterministic engine tests."""

from collections import defaultdict

from app.core.errors import AppError
from app.providers.llm.base import LLMProvider, Message


class ScriptedLLM(LLMProvider):
    """Returns queued objects per task; records every prompt it sees."""

    name = "scripted"

    def __init__(self, script: dict[str, list] | None = None) -> None:
        self.script: dict[str, list] = defaultdict(list, script or {})
        self.calls: list[tuple[str, list[Message]]] = []
        self._last_delta: dict | None = None

    def push(self, task: str, *items) -> None:
        self.script[task].extend(items)

    async def complete_json(self, schema, messages, *, task, role="conversation",
                            temperature=None, max_tokens=None):
        self.calls.append((task, messages))
        if task == "state" and not self.script[task]:
            # Notes run off the critical path; by default replay the scripted turn's delta.
            return schema.model_validate(self._last_delta or {})
        if not self.script[task]:
            raise AssertionError(f"no scripted response for {task}")
        item = self.script[task].pop(0)
        if task == "turn" and not isinstance(item, AppError):
            data = item if isinstance(item, dict) else item.model_dump()
            self._last_delta = data.get("state_delta")
        if isinstance(item, AppError):
            raise item
        return schema.model_validate(item if isinstance(item, dict) else item.model_dump())

    async def complete_text(self, messages, *, task, role="conversation", temperature=None,
                            max_tokens=None):
        return ""

    def last_prompt(self, task: str) -> str:
        # The turn prompt is split into a cached prefix and the part that changes.
        msgs = next(m for t, m in reversed(self.calls) if t == task)
        return "\n\n".join(m.content for m in msgs if m.role == "user")


# ---- speech and telephony fakes -------------------------------------------------------
import asyncio as _asyncio

from app.providers.stt.base import STTEvent, STTProvider, STTStream
from app.providers.tts.base import TTSProvider, Voice


class FakeSTTStream(STTStream):
    """Counts audio received; tests push the transcripts the caller "said"."""

    def __init__(self) -> None:
        self.received = 0
        self.closed = False
        self._q: _asyncio.Queue[STTEvent | None] = _asyncio.Queue()

    async def send_pcm(self, pcm16: bytes) -> None:
        self.received += len(pcm16)

    def say(self, text: str, *, partial: str | None = None, language: str = "en-IN") -> None:
        if partial:
            self._q.put_nowait(STTEvent("partial", text=partial, utterance=1))
        self._q.put_nowait(STTEvent("final", text=text, language_code=language, utterance=1))

    def partial(self, text: str) -> None:
        self._q.put_nowait(STTEvent("partial", text=text, utterance=2))

    async def events(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        self.closed = True
        self._q.put_nowait(None)


class FakeSTT(STTProvider):
    name = "fake"

    def __init__(self, transcript: str = "Call Rahul and negotiate the rent.",
                 language: str = "en-IN") -> None:
        self.transcript, self.language = transcript, language
        self.streams: list[FakeSTTStream] = []
        self.uploads: list[bytes] = []

    async def open_stream(self, language_code: str = "auto") -> FakeSTTStream:
        s = FakeSTTStream()
        self.streams.append(s)
        return s

    async def transcribe(self, audio, filename="audio.wav", language_code="unknown"):
        self.uploads.append(audio)
        return STTEvent("final", text=self.transcript, language_code=self.language)


class FakeTTS(TTSProvider):
    """0.2 s of silence per line, in two chunks."""

    name = "fake"

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self.lines: list[str] = []

    def supports(self, language_code: str) -> bool:
        return True

    async def synthesize(self, text: str, voice: Voice):
        self.lines.append(text)
        half = b"\x00\x00" * (self.sample_rate // 10)
        yield half
        yield half

    def warm(self) -> None:
        pass


class FakeExotel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def connect_call(self, **kw) -> dict:
        self.calls.append(kw)
        return {"Sid": f"call_{len(self.calls)}", "Status": "queued"}

    async def get_call(self, sid: str) -> dict:
        return {"Sid": sid, "Status": "in-progress"}
