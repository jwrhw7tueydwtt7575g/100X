"""Application settings, loaded from environment variables / `.env`."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# pydantic-settings JSON-decodes list fields inside the env source, before any
# validator runs — so `A,B` raises there and our validator never sees it.
# NoDecode hands the raw string over instead.
CsvList = Annotated[list[str], NoDecode]

Environment = Literal["local", "dev", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- app ---------------------------------------------------------------
    app_name: str = "WariVerse API"
    app_version: str = "1.0.0"
    environment: Environment = "local"
    debug: bool = False
    api_prefix: str = "/api"

    # --- database ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://wariverse:wariverse@localhost:5432/wariverse"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_echo: bool = False

    # --- redis -------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 300
    session_ttl_seconds: int = 60 * 60 * 24

    # --- llm ---------------------------------------------------------------
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    openai_timeout_seconds: float = 20.0
    llm_enabled: bool = True
    llm_max_history_turns: int = 8

    # --- mapbox ------------------------------------------------------------
    mapbox_access_token: str | None = None

    # --- auth --------------------------------------------------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 30  # 30 days
    otp_ttl_seconds: int = 600  # 10 minutes
    otp_max_attempts: int = 5
    otp_length: int = 6
    otp_debug_echo: bool = True
    # Max OTPs per phone per rolling window — SMS costs money and an unmetered
    # send endpoint is an SMS-bombing tool.
    otp_rate_limit: int = 3
    otp_rate_window_seconds: int = 3600

    # --- sms ---------------------------------------------------------------
    # console | fast2sms | twilio
    sms_provider: str = "console"
    fast2sms_api_key: str | None = None
    fast2sms_sender_id: str = "FSTSMS"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None
    sms_timeout_seconds: float = 10.0

    # --- speech to text ----------------------------------------------------
    deepgram_api_key: str | None = None
    deepgram_model: str = "nova-2"
    # Rejected before any paid API call is made.
    voice_max_upload_bytes: int = 10 * 1024 * 1024
    voice_max_duration_seconds: int = 60
    voice_timeout_seconds: float = 30.0

    # --- text to speech ----------------------------------------------------
    elevenlabs_api_key: str | None = None
    # "Aria" — verify against your account's voice list before launch; voice
    # ids are per-account for cloned voices and global for stock ones.
    elevenlabs_voice_id: str = "9BWtsMINqrJLrRacOk9x"
    elevenlabs_model: str = "eleven_multilingual_v2"
    # Marathi: ElevenLabs handles it poorly, so it goes to Google WaveNet.
    # Either a service-account JSON path or a plain API key works.
    google_application_credentials: str | None = None
    google_tts_api_key: str | None = None
    google_tts_voice_mr: str = "mr-IN-Wavenet-A"
    tts_cache_ttl_seconds: int = 86_400  # 24 hours
    tts_max_characters: int = 1000

    # --- ivr ---------------------------------------------------------------
    # Twilio webhooks are public URLs, so every request is signature-checked
    # against TWILIO_AUTH_TOKEN. See app/services/twilio_signature.py.
    ivr_validate_signature: bool = True
    # The externally visible base URL Twilio calls. Signature validation hashes
    # the URL, and behind a proxy `request.url` is often http:// internally,
    # which would never match. Set this to the public https:// origin.
    ivr_public_base_url: str | None = None
    ivr_speech_timeout_seconds: int = 8
    ivr_max_turns: int = 30
    ivr_hold_music_url: str = (
        "http://com.twilio.music.classical.s3.amazonaws.com/BusyStrings.wav"
    )

    # --- cors --------------------------------------------------------------
    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:8081", "http://127.0.0.1:8081"]
    )
    # `https://*.wariverse.app` is a wildcard, which the CORS spec cannot express
    # as a literal origin — Starlette matches it with a regex instead.
    cors_origin_regex: str = r"^https://([a-z0-9-]+\.)*wariverse\.app$"

    # --- logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # --- admin -------------------------------------------------------------
    # Unset means the admin endpoints refuse every request. There is no
    # "no key configured, so allow everything" mode.
    admin_api_key: str | None = None

    # --- crowd simulator ---------------------------------------------------
    # Stand-in for the CCTV feed. Run it in ONE process only — see
    # app/services/crowd_simulator.py.
    crowd_simulator_enabled: bool = True
    crowd_simulator_interval_seconds: int = 300

    # --- domain ------------------------------------------------------------
    default_language: str = "mr"
    supported_languages: CsvList = Field(
        default_factory=lambda: ["mr", "hi", "en", "kn", "te"]
    )
    facility_default_radius_m: int = 10000
    facility_max_radius_m: int = 50000
    facility_categories: CsvList = Field(
        default_factory=lambda: [
            "medical", "water", "toilet", "rest", "food", "accommodation",
        ]
    )
    # 2.5 km/h, not the usual 5: during the Wari the crowd sets the pace.
    walking_speed_kmph: float = 2.5
    emergency_helpline: str = "112"
    wari_control_room: str = "1800-233-1000"
    # Optional: an SMS goes here on every SOS when set and SMS_PROVIDER is
    # configured. Unset means the Redis dashboard feed is the only alert path.
    control_room_phone: str | None = None

    @field_validator("cors_origins", "supported_languages", "facility_categories", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept `a,b,c` as well as `["a","b","c"]`.

        pydantic-settings parses list fields as JSON, so the comma-separated
        form every deployment guide uses would otherwise crash on startup —
        a confusing failure for something as routine as setting CORS origins.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            # NoDecode turned off the automatic JSON parse, so do it here —
            # the JSON form is what `.env.example` has always used.
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"expected JSON array or comma-separated list: {exc}")
        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("database_url")
    @classmethod
    def _force_async_driver(cls, value: str) -> str:
        """Accept the plain `postgresql://` URL that most PaaS providers hand out."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def insecure_secrets(self) -> list[str]:
        """Configuration that must not reach production. Checked at startup."""
        problems: list[str] = []
        if self.jwt_secret == "change-me-in-production":
            problems.append("JWT_SECRET is still the default value")
        elif len(self.jwt_secret) < 32:
            problems.append(
                f"JWT_SECRET is {len(self.jwt_secret)} characters; use at least 32"
            )
        if not self.admin_api_key:
            problems.append("ADMIN_API_KEY is unset — admin endpoints will refuse all")
        if self.ivr_validate_signature and not self.twilio_auth_token:
            problems.append("TWILIO_AUTH_TOKEN is unset — the IVR webhooks will 503")
        return problems

    @property
    def expose_debug_otp(self) -> bool:
        """Never leak OTPs in production, whatever the env var says."""
        return self.otp_debug_echo and not self.is_production

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_enabled and self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
