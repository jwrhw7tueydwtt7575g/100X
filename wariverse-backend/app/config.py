"""Application settings, loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    twilio_from_number: str | None = None
    sms_timeout_seconds: float = 10.0

    # --- cors --------------------------------------------------------------
    cors_origins: list[str] = Field(
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
    supported_languages: list[str] = Field(
        default_factory=lambda: ["mr", "hi", "en", "kn", "te"]
    )
    facility_default_radius_m: int = 10000
    facility_max_radius_m: int = 50000
    facility_categories: list[str] = Field(
        default_factory=lambda: [
            "medical", "water", "toilet", "rest", "food", "accommodation",
        ]
    )
    # 2.5 km/h, not the usual 5: during the Wari the crowd sets the pace.
    walking_speed_kmph: float = 2.5
    emergency_helpline: str = "112"
    wari_control_room: str = "1800-233-1000"

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
