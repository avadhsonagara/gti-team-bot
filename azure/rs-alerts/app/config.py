"""
Single source of truth for RS Alerts runtime configuration.

Reads values from environment variables (Azure Function App Settings are
exposed as env vars at runtime) and, for local development, from a .env file.
Field names map to env vars via automatic uppercasing (e.g. `gti_api_key`
reads from `GTI_API_KEY`), except where an explicit `validation_alias` is
set below for names that don't follow that convention.
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

    # ── Bot Framework identity ───────────────────────────────────────────────
    # CLIENT_ID + MANAGED_IDENTITY_CLIENT_ID authenticate outbound calls to
    # the Bot Framework Connector API via the same User-Assigned Managed
    # Identity the bot's Azure Bot resource uses as its App ID (msaAppType
    # "UserAssignedMSI") — no client secret, ever. This same identity is also
    # used to call Microsoft Graph for Teams app auto-install
    # (app/graph_client.py) — it needs the
    # TeamsAppInstallation.ReadWriteForTeam.All application permission
    # granted with admin consent for that to work.
    client_id: str = ""
    managed_identity_client_id: str = ""

    # ── Microsoft Teams target ───────────────────────────────────────────────
    teams_channel_id: str = ""
    # Microsoft's own documented global routing alias for proactive messages
    # (RS Alerts always posts proactively, with no incoming activity to read
    # a region-specific serviceUrl from) — resolves the correct region
    # internally, so this is NOT a region-specific guess:
    # https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/
    # conversations/send-proactive-messages ("If the serviceUrl isn't
    # available... use https://smba.trafficmanager.net/teams/"). Previously
    # hardcoded to the Americas-specific endpoint (.../amer/), which silently
    # failed for tenants outside that region.
    service_url: str = "https://smba.trafficmanager.net/teams/"

    # ── Google Threat Intelligence (GTI) Alerts API ──────────────────────────
    gti_api_key: str = ""
    gti_rsa_project: str = ""
    page_size: int = 1000

    # ── Alert filters (comma-separated levels; empty = no filter on that field) ──
    filter_severity_level: str = "MEDIUM,HIGH"
    filter_priority_level: str = "MEDIUM,HIGH,CRITICAL"
    filter_relevance_level: str = "MEDIUM,HIGH"
    filter_relevance_confidence: str = "MEDIUM,HIGH"

    # ── Cursor state (Azure Blob Storage) ────────────────────────────────────
    # Flex Consumption instances are ephemeral and may scale to zero between
    # timer ticks, so the incremental cursor cannot live on local disk (as the
    # original gti-alerts/state.json did) — it's persisted as a blob instead,
    # in the same storage account the Function App already uses.
    azure_web_jobs_storage: str = Field(default="", validation_alias="AzureWebJobsStorage")
    state_container_name: str = "rs-alerts-state"
    state_blob_name: str = "cursor.json"

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_key", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """Trim whitespace from secret-like values."""
        return (v or "").strip()

    @field_validator("service_url", mode="before")
    @classmethod
    def normalize_service_url(cls, v: str) -> str:
        """Ensure the Bot Framework service URL ends with exactly one slash."""
        val = (v or "https://smba.trafficmanager.net/amer/").strip()
        return val.rstrip("/") + "/"


settings = Settings()
