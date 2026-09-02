"""
Single source of truth for RS Alerts runtime configuration on Google Cloud Platform.

Reads values from environment variables and, for local development, from a .env file.
Field names map to env vars via automatic uppercasing (e.g. `gti_api_key`
reads from `GTI_API_KEY`).
"""
import os
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
    # Authenticates outbound calls to the Bot Framework Connector API via
    # Microsoft Entra ID (OAuth2 client credentials). This same app
    # registration is also used to call Microsoft Graph for Teams app
    # auto-install (app/graph_client.py) — it needs the
    # TeamsAppInstallation.ReadWriteForTeam.All application permission
    # granted with admin consent for that to work.
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""

    # ── Microsoft Teams target ───────────────────────────────────────────────
    teams_channel_id: str = ""
    service_url: str = "https://smba.trafficmanager.net/amer/"

    # ── Google Threat Intelligence (GTI) Alerts API ──────────────────────────
    gti_api_key: str = ""
    # Intentionally has no fallback to gcp_project_id/GCP_PROJECT_ID: the GTI
    # project and the GCP project hosting this infrastructure are different
    # concepts and are rarely the same value. Leaving this unset must fail
    # the required-config check in job.py rather than silently defaulting to
    # the wrong project.
    gti_rsa_project: str = ""
    page_size: int = 1000

    # ── Alert filters (comma-separated levels, empty = no filter on that field) ──
    filter_severity_level: str = ""
    filter_priority_level: str = ""
    filter_relevance_level: str = ""
    filter_relevance_confidence: str = ""

    # ── Google Cloud Platform & Firestore Cursor State ────────────────────────
    gcp_project_id: str = Field(
        default_factory=lambda: (
            os.getenv("GCP_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
            or "gtimsteamaiintegration-3898"
        )
    )
    firestore_database: str = "(default)"
    firestore_state_collection: str = "rs-alerts-state"
    firestore_state_doc: str = "cursor"

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_key", "client_secret", mode="before")
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
