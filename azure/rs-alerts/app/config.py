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

    # ── Microsoft Teams delivery ──────────────────────────────────────────────
    # RS Alerts posts via a Teams incoming webhook (Workflows/Power Automate),
    # not the Bot Framework Connector API — no Azure AD credentials or bot
    # team-membership needed, just this URL.
    rs_alerts_webhook_url: str = ""

    # ── Google Threat Intelligence (GTI) Alerts API ──────────────────────────
    gti_api_key: str = ""
    gti_rsa_project: str = ""
    backfill_days: int = 7
    page_size: int = 1000

    # ── Alert filters (comma-separated levels) ───────────────────────────────
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

    @field_validator("gti_api_key", "rs_alerts_webhook_url", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """Trim whitespace from secret-like values."""
        return (v or "").strip()


settings = Settings()
