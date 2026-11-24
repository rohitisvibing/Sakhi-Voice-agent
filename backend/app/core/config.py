"""Application settings loaded from environment variables (server-side only)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_ENV, ".env"), extra="ignore")

    # LLM (OpenRouter)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-sonnet-5.5"
    llm_model_extraction: str = ""
    llm_model_conversation: str = ""
    llm_model_simulator: str = ""
    # Live turns (the agent's next line, its state update): speed matters most here.
    llm_model_live: str = "anthropic/claude-haiku-5.5"
    llm_temperature: float = 0.4
    llm_max_tokens: int = 1800
    llm_timeout_s: float = 60.0
    # Live turns (agent reply, simulated counterparty) must not hang the call: fail fast, retry.
    llm_timeout_live_s: float = 20.0

    # Speech (Sarvam)
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_ws_url: str = "wss://api.sarvam.ai"
    sarvam_stt_model: str = "saaras:v3-realtime"
    sarvam_stt_rest_model: str = "saaras:v4"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_agent_speaker: str = "priya"
    sarvam_tts_counterparty_speaker: str = "rahul"
    sarvam_tts_sample_rate: int = 22050
    sarvam_translate_model: str = "mayura:v1"

    # Persistence (Supabase)
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""

    # App
    demo_mode: bool = False
    # Sign-in (Supabase Auth with Google). Required whenever SUPABASE_URL is set, unless disabled.
    auth_disabled: bool = False
    supabase_jwt_secret: str = ""  # only for legacy HS256-signed projects (optional)
    # Per-user daily caps on the calls that spend LLM and speech credits (0 = no cap).
    max_missions_per_day: int = 30
    max_conversations_per_day: int = 20

    # Billing (Razorpay subscription). Off when the keys are unset: everything is free.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_plan_id: str = ""
    # Comma-separated sign-in emails that never pay (checked against the verified sign-in
    # email, not the editable profile).
    free_accounts: str = ""
    plan_price_inr: int = 499
    monthly_talk_minutes: int = 180  # never shown in the UI
    trial_talk_minutes: int = 3      # the free mission's hidden cap, across its calls
    # Phone calls (Exotel). Off when the keys are unset. Never sent to the browser.
    exotel_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_subdomain: str = "api.exotel.com"  # api.in.exotel.com for Mumbai-region accounts
    exotel_caller_id: str = ""  # your ExoPhone
    telephony_secret: str = ""  # signs the stream and status-callback URLs given to Exotel
    public_base_url: str = "https://www.sakhi.buzz"
    # Calls run inside one Vercel function (300 s cap): Sakhi wraps up before it.
    exotel_simulate: bool = False  # local testing: no real call, scripts/exotel_sim.py answers
    phone_call_limit_seconds: int = 240
    phone_ring_timeout_s: int = 40
    ivr_pause_ms: int = 500
    text_pacing: bool = True  # text-only mode waits ~reading time between turns
    # Practice mode: compute the next line while the current one is still being spoken.
    speculate: bool = True
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    default_principal_id: str = "00000000-0000-0000-0000-000000000001"

    def model_for(self, role: str) -> str:
        specific = {
            "extraction": self.llm_model_extraction,
            "conversation": self.llm_model_conversation,
            "simulator": self.llm_model_simulator,
            "live": self.llm_model_live or self.llm_model_conversation,
        }.get(role, "")
        return specific or self.openrouter_model

    @property
    def llm_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def speech_configured(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def free_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.free_accounts.split(",") if e.strip()}

    @property
    def billing_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_plan_id)

    @property
    def exotel_configured(self) -> bool:
        return bool(self.exotel_sid and self.exotel_api_key and self.exotel_api_token
                    and self.exotel_caller_id and self.telephony_secret)

    @property
    def phone_available(self) -> bool:
        return bool(self.telephony_secret) and (self.exotel_configured or self.exotel_simulate)

    @property
    def db_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
