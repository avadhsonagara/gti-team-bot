"""
Single source of truth for runtime configuration.

Reads values from environment variables (and .env at startup).
Field names map to env vars via automatic uppercasing:
  e.g. `client_id` reads from `CLIENT_ID`.
"""
from pydantic import field_validator
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
    gti_api_key: str = ""
    gti_api_base_url: str = "https://www.virustotal.com/api/v3"
    gti_max_rpm: int = 5
    gti_rate_limit_window_seconds: float = 60.0

    # ── Local Persistence ──────────────────────────────────────────────────────
    # Custom output-format instructions and per-thread GTI session mappings
    # both live in this one JSON file — see app/output_format_store.py and
    # app/gti/session_store.py.
    local_store_path: str = "local_store.json"

    # Deploy-time default used to seed the local store on first read
    output_format_instructions: str = ""

    # ── Microsoft Graph (channel thread context) ──────────────────────────────
    # Requires the CLIENT_ID app registration to be granted the application
    # permission ChannelMessage.Read.All with tenant-admin consent. Channel-only —
    # Teams has no equivalent thread concept for personal/group chats.
    thread_context_enabled: bool = True
    thread_context_message_count: int = 5

    # ── Server & Observability ────────────────────────────────────────────────
    port: int = 8080
    ssl_keyfile: str = ""
    ssl_certfile: str = ""

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_base_url", mode="before")
    @classmethod
    def strip_and_normalize_url(cls, v: str) -> str:
        """Trim whitespace and trailing slashes from the API base URL."""
        val = (v or "https://www.virustotal.com/api/v3").strip()
        return val.rstrip("/")

    @field_validator("gti_api_key", "client_secret", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """Trim whitespace from secret values."""
        return (v or "").strip()


settings = Settings()
