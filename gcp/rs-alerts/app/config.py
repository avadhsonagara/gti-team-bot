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

    # ── Microsoft Teams delivery ──────────────────────────────────────────────
    # RS Alerts posts via a Teams incoming webhook (Workflows/Power Automate),
    # not the Bot Framework Connector API — no Azure AD credentials or bot
    # team-membership needed, just this URL.
    rs_alerts_webhook_url: str = ""

    # ── Google Threat Intelligence (GTI) Alerts API ──────────────────────────
    gti_api_key: str = ""
    # Intentionally has no fallback to gcp_project_id/GCP_PROJECT_ID: the GTI
    # project and the GCP project hosting this infrastructure are different
    # concepts and are rarely the same value. Leaving this unset must fail
    # the required-config check in job.py rather than silently defaulting to
    # the wrong project.
    gti_rsa_project: str = ""
    backfill_days: int = 7
    page_size: int = 1000

    # ── Alert filters (comma-separated levels) ───────────────────────────────
    filter_severity_level: str = "MEDIUM,HIGH"
    filter_priority_level: str = "MEDIUM,HIGH,CRITICAL"
    filter_relevance_level: str = "MEDIUM,HIGH"
    filter_relevance_confidence: str = "MEDIUM,HIGH"

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

    @field_validator("gti_api_key", "rs_alerts_webhook_url", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """Trim whitespace from secret-like values."""
        return (v or "").strip()


settings = Settings()
