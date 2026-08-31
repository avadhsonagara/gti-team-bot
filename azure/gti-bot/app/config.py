"""
Single source of truth for runtime configuration.

Reads values from environment variables (and .env at startup).
Field names map to env vars via automatic uppercasing:
  e.g. `client_id` reads from `CLIENT_ID`.
"""
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── Microsoft Teams / Bot Framework ─────────────────────────────────────────
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""

    # ── Google Threat Intelligence (GTI) Agentic API ────────────────────────────
    # API key authenticated via the x-apikey header
    gti_api_key: str = ""
    gti_api_base_url: str = "https://www.virustotal.com/api/v3"
    gti_timeout_seconds: float = 180.0
    gti_max_retries: int = 2
    gti_retry_delay: float = 2.0

    # ── Output format instructions ───────────────────────────────────────────
    # Deploy-time default (main.bicep's outputFormatInstructions param), used
    # to seed the JSON config blob on first read — see app/output_format_store.py.
    output_format_instructions: str = ""
    azure_web_jobs_storage: str = Field(default="", validation_alias="AzureWebJobsStorage")

    # ── Server ────────────────────────────────────────────────────────────────
    port: int = 8080
    log_format: str = ""
    ssl_keyfile: str = ""
    ssl_certfile: str = ""

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_base_url", mode="before")
    @classmethod
    def strip_and_normalize_url(cls, v: str) -> str:
        """Trim whitespace and trailing slashes from the API base URL."""
        val = (v or "https://www.virustotal.com/api/v3").strip()
        return val.rstrip("/")

    @field_validator("gti_api_key", mode="before")
    @classmethod
    def strip_api_key(cls, v: str) -> str:
        """Trim whitespace from the configured GTI API key."""
        return (v or "").strip()


settings = Settings()
