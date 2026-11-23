"""Provider/repository wiring. Swap implementations here without touching the engine."""

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.demo import DemoLLM
from app.providers.llm.openrouter import OpenRouterProvider
from app.providers.stt.base import STTProvider
from app.providers.stt.sarvam import SarvamSTT
from app.providers.telephony.exotel import Exotel, LocalExotel
from app.providers.translate.base import TranslationProvider
from app.providers.translate.sarvam import SarvamTranslator
from app.providers.tts.base import TTSProvider
from app.providers.tts.sarvam import SarvamTTS
from app.repositories.base import Repository
from app.repositories.memory import MemoryRepository
from app.repositories.supabase_repo import SupabaseRepository

log = logging.getLogger(__name__)
PHONE_SAMPLE_RATE = 16000  # Exotel streams at 16 kHz when asked (?sample-rate=16000)


@dataclass
class Container:
    settings: Settings
    llm: LLMProvider
    stt: STTProvider
    tts: TTSProvider
    repo: Repository
    translator: TranslationProvider | None = None
    payments: Any = None  # a Razorpay client; tests swap in a fake
    telephony: Any = None  # an Exotel client; tests swap in a fake
    phone_tts: TTSProvider | None = None  # TTS rendered at the phone line's sample rate

    @property
    def demo(self) -> bool:
        return self.llm.is_demo

    @property
    def voice_available(self) -> bool:
        return bool(self.settings.sarvam_api_key)


def build_container(settings: Settings | None = None) -> Container:
    s = settings or get_settings()
    use_demo = s.demo_mode or not s.openrouter_api_key
    if use_demo:
        log.warning("OpenRouter key missing or DEMO_MODE=true: running the demo LLM.")
    if s.db_configured:
        repo: Repository = SupabaseRepository(s)
    else:
        log.warning("Supabase not configured: using the in-memory repository (data is lost "
                    "on restart).")
        repo = MemoryRepository(seed_principal_id=s.default_principal_id)
    llm = DemoLLM() if use_demo else OpenRouterProvider(s)
    telephony: Any = None
    if s.exotel_simulate:
        telephony = LocalExotel()
    elif s.exotel_configured:
        telephony = Exotel(s)
    phone_tts = SarvamTTS(s.model_copy(update={"sarvam_tts_sample_rate": PHONE_SAMPLE_RATE}))
    return Container(settings=s, llm=llm, stt=SarvamSTT(s), tts=SarvamTTS(s), repo=repo,
                     translator=SarvamTranslator(s, fallback=llm), phone_tts=phone_tts,
                     telephony=telephony)


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def set_container(c: Container) -> None:
    global _container
    _container = c
