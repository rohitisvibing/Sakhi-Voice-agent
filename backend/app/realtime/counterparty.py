"""Counterparty channels: the seam where telephony will plug in later.

The session never needs to know who is on the other end. A channel decides what happens
after the agent finishes speaking and how counterparty speech arrives.

- SimulatedChannel: an LLM persona replies (practice mode, no telephony).
- BrowserMicChannel: a real person speaks into the browser microphone (live mode).
- PhoneChannel (phone.py): a phone call through Exotel; same STT/TTS pipeline, the call's
  audio instead of the browser's.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.models.engine import SimulatorPersona
from app.services import simulator

if TYPE_CHECKING:
    from .session import LiveSession


class CounterpartyChannel(ABC):
    kind: str
    uses_microphone: bool = False
    handles_audio: bool = False  # True: the channel plays the agent's speech itself

    async def play(self, session: "LiveSession", event, voice) -> None:
        raise NotImplementedError

    async def press(self, session: "LiveSession", digits: str) -> None:
        """Keypad tones (phone only)."""

    def heard_speech(self, session: "LiveSession", final: bool = False) -> None:
        """The other side said something (a partial transcript, or a `final` one)."""

    async def close(self, session: "LiveSession") -> None:
        """The conversation is over (a phone channel hangs up)."""

    @abstractmethod
    async def prepare(self, session: "LiveSession") -> None: ...

    @abstractmethod
    async def after_agent_turn(self, session: "LiveSession") -> None:
        """Called when the agent has finished speaking and expects a reply."""


class SimulatedChannel(CounterpartyChannel):
    kind = "simulated"

    def __init__(self) -> None:
        self.persona: SimulatorPersona | None = None

    async def prepare(self, session: "LiveSession") -> None:
        if self.persona is None:
            self.persona = await simulator.create_persona(session.llm, session.mission.spec)

    async def after_agent_turn(self, session: "LiveSession") -> None:
        session.schedule(session.run_simulator_turn(), kind="simulator")


class BrowserMicChannel(CounterpartyChannel):
    kind = "live"
    uses_microphone = True

    async def prepare(self, session: "LiveSession") -> None:
        return None

    async def after_agent_turn(self, session: "LiveSession") -> None:
        await session.set_state_listening()
