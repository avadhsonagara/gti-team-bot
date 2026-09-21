"""
Runtime configuration settings for the RS Alerts Function App.

Loads application settings from environment variables and local .env files,
providing validated configuration for GTI alerts, Microsoft Teams, Azure Storage, and Graph.
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
    teams_channel_link_or_id: str = ""
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
    # Backfill window (days) used to seed the cursor on the very first run
    # (no persisted state yet) — bounds how much alert history a fresh
    # deployment pulls in, instead of the project's entire history. Clamped
    # to 1-7 at runtime (see job.py) if set outside that range.
    backfill_days: int = 7

    # ── Alert filters (comma-separated levels) ───────────────────────────────
    # Each field must resolve to at least one value — there is no "disable
    # this dimension" option; set every valid level for a field to pass
    # everything on that dimension instead.
    filter_severity_level: str = "MEDIUM,HIGH"
    filter_priority_level: str = "MEDIUM,HIGH,CRITICAL"
    filter_relevance_level: str = "MEDIUM,HIGH"
    filter_relevance_confidence: str = "MEDIUM,HIGH"

    # ── Cursor state (Azure Blob Storage) ────────────────────────────────────
    # Flex Consumption instances are ephemeral and may scale to zero between
    # timer ticks, so the incremental cursor cannot live on local disk — it's
    # persisted as a blob instead, in the same storage account the Function
    # App already uses.
    azure_web_jobs_storage: str = Field(default="", validation_alias="AzureWebJobsStorage")
    state_container_name: str = "rs-alerts-state"
    state_blob_name: str = "cursor.json"

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_key", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """
        Trim leading and trailing whitespace from secret-like configuration values.

        Args:
            v: Input secret string.

        Returns:
            Cleaned secret string.
        """
        return (v or "").strip()

    @field_validator("service_url", mode="before")
    @classmethod
    def normalize_service_url(cls, v: str) -> str:
        """
        Normalize the Bot Framework service URL to end with a single trailing slash.

        Args:
            v: Input service URL string.

        Returns:
            Normalized URL string ending with '/'.
        """
        val = (v or "https://smba.trafficmanager.net/teams/").strip()
        return val.rstrip("/") + "/"



settings = Settings()
