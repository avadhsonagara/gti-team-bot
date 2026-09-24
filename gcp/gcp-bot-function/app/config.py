"""
Single source of truth for runtime configuration on Google Cloud Platform.

Reads values from environment variables (and .env at startup).
Field names map to env vars via automatic uppercasing:
  e.g. `client_id` reads from `CLIENT_ID`.
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

    # ── Microsoft Teams / Bot Framework ─────────────────────────────────────────
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""

    # ── Google Threat Intelligence (GTI) Agentic API ────────────────────────────
    gti_api_key: str = ""
    gti_api_base_url: str = ""
    # Read timeout (seconds) for a single GTI Agentic API call.
    gti_timeout_seconds: float = 480.0

    # ── Google Cloud Platform / Firestore Persistence ─────────────────────────
    gcp_project_id: str = Field(
        default_factory=lambda: (
            os.getenv("GCP_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
            or ""
        )
    )
    firestore_database: str = "(default)"
    # Bot config (output-format doc) and per-thread GTI session documents
    # both live in this one collection — see app/output_format_store.py and
    # app/gti/session_store.py.
    firestore_bot_config_collection: str = "gti-bot-config"
    firestore_output_format_doc: str = "gti-custom-output-format"

    # Deploy-time default used to seed the Firestore document on first read
    output_format_instructions: str = ""

    # ── Microsoft Graph (channel thread context) ──────────────────────────────
    # Requires the CLIENT_ID app registration to be granted the application
    # permission ChannelMessage.Read.All with tenant-admin consent. Channel-only —
    # Teams has no equivalent thread concept for personal/group chats.
    thread_context_message_count: int = 5

    # ── HTTP connection pool sizing ───────────────────────────────────────────
    # Reuses the THREADS env var terraform/main.tf already sets from
    # var.concurrency (Cloud Run's max_instance_request_concurrency) rather
    # than introducing a second, separate setting — so every outbound HTTP
    # client's connection pool (Bot Framework Connector, GTI, Graph) can hold
    # one connection per concurrent request this instance is actually given,
    # instead of urllib3's default pool size of 10.
    concurrent_requests: int = Field(default=40, validation_alias="THREADS")

    # ── Inbound request guard ─────────────────────────────────────────────────
    # /api/messages is publicly reachable (--allow-unauthenticated — the
    # bot's own Bearer-token check is what actually gates it), so it will get
    # arbitrary/abusive traffic. Reject an implausibly large body outright,
    # before spending any CPU parsing it as JSON.
    max_request_body_bytes: int = 1 * 1024 * 1024

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("gti_api_base_url", mode="before")
    @classmethod
    def strip_and_normalize_url(cls, v: str) -> str:
        """Trim whitespace and trailing slashes from the API base URL."""
        val = (v or "").strip()
        return val.rstrip("/")

    @field_validator("gti_api_key", "client_secret", mode="before")
    @classmethod
    def strip_secret(cls, v: str) -> str:
        """Trim whitespace from secret values."""
        return (v or "").strip()

    @field_validator("thread_context_message_count")
    @classmethod
    def clamp_thread_context_message_count(cls, v: int) -> int:
        """Clamp the thread context message count between 1 and 30."""
        return max(1, min(v, 30))


settings = Settings()
