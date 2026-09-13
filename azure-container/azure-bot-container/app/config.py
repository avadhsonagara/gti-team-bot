"""
Single source of truth for runtime configuration.

Reads values from environment variables (and .env at startup). Azure
Function App Application Settings are exposed as environment variables at
runtime, so no Azure-specific config loading is needed.
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
    # CLIENT_ID + MANAGED_IDENTITY_CLIENT_ID authenticate via the same
    # User-Assigned Managed Identity the Azure Bot resource uses as its App ID
    # (msaAppType "UserAssignedMSI") — no client secret, ever. This same
    # identity is also used for Microsoft Graph calls (app/graph/client.py)
    # and for the bot's own Connector API token (app/teams/bot_client.py).
    client_id: str = ""
    managed_identity_client_id: str = ""

    # ── Google Threat Intelligence (GTI) Agentic API ────────────────────────────
    # API key authenticated via the x-apikey header
    gti_api_key: str = ""
    gti_api_base_url: str = "https://www.virustotal.com/api/v3"
    # Read timeout for a single GTI Agentic API call (app/gti/client.py). Must
    # stay comfortably above the slowest real query — a container has no
    # platform-imposed HTTP ceiling like Azure Functions' 230s, so this is the
    # only thing that can cut a slow-but-working call short.
    gti_timeout_seconds: float = 600.0

    # ── Output format instructions ───────────────────────────────────────────
    # Deploy-time default (main.bicep's outputFormatInstructions param), used
    # to seed the JSON config blob on first read — see app/output_format_store.py.
    output_format_instructions: str = ""
    # Backs both app/output_format_store.py and app/gti/session_store.py.
    # Named generically (not "AzureWebJobsStorage", an Azure Functions-runtime
    # convention) since this container has no Functions host.
    storage_connection_string: str = Field(default="", validation_alias="STORAGE_CONNECTION_STRING")

    # ── Microsoft Graph (channel thread context) ──────────────────────────────
    # Requires the Managed Identity above to be granted the Graph APPLICATION
    # permission ChannelMessage.Read.All with tenant-admin consent.
    # Channel-only — Teams has no equivalent thread concept for personal/
    # group chats.
    thread_context_enabled: bool = True
    thread_context_message_count: int = 5

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_base_url", mode="before")
    @classmethod
    def strip_and_normalize_url(cls, v: str) -> str:
        """Trim whitespace and trailing slashes from the API base URL."""
        val = (v or "https://www.virustotal.com/api/v3").strip()
        return val.rstrip("/")

    @field_validator("gti_api_key", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """Trim whitespace from secret-like values."""
        return (v or "").strip()


settings = Settings()
