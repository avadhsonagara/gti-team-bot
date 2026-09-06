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
    # In Azure, CLIENT_ID + MANAGED_IDENTITY_CLIENT_ID authenticate via the same
    # User-Assigned Managed Identity the Azure Bot resource uses as its App ID
    # (msaAppType "UserAssignedMSI") — no client secret involved in production.
    # CLIENT_SECRET is only a local-dev fallback (classic app registration
    # flow), since managed identity isn't available outside Azure. This same
    # identity is also used for Microsoft Graph calls (app/graph/client.py,
    # channel thread context) — see app/config.py's thread_context_* comment.
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""
    managed_identity_client_id: str = ""

    # ── Google Threat Intelligence (GTI) Agentic API ────────────────────────────
    # API key authenticated via the x-apikey header
    gti_api_key: str = ""
    gti_api_base_url: str = "https://www.virustotal.com/api/v3"
    gti_max_rpm: int = 5
    gti_rate_limit_window_seconds: float = 60.0

    # ── Output format instructions ───────────────────────────────────────────
    # Deploy-time default (main.bicep's outputFormatInstructions param), used
    # to seed the JSON config blob on first read — see app/output_format_store.py.
    output_format_instructions: str = ""
    azure_web_jobs_storage: str = Field(default="", validation_alias="AzureWebJobsStorage")

    # ── Microsoft Graph (channel thread context) ──────────────────────────────
    # Requires whichever identity is configured above (managed identity in
    # Azure, or the CLIENT_ID app registration for local dev) to be granted
    # the Graph APPLICATION permission ChannelMessage.Read.All with
    # tenant-admin consent. Channel-only — Teams has no equivalent thread
    # concept for personal/group chats.
    thread_context_enabled: bool = True
    thread_context_message_count: int = 5
    thread_context_log_file: str = "thread_context.txt"

    # ── Server ────────────────────────────────────────────────────────────────
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
        """Trim whitespace from secret-like values."""
        return (v or "").strip()


settings = Settings()
