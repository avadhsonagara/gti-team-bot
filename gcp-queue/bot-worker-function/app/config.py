"""
Single source of truth for runtime configuration on the Worker Cloud Function.

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
    # Must be the SAME app registration bot-ingest-function/.env uses — both
    # Cloud Functions authenticate as one bot. Also this function's own
    # identity check when matching a channel thread message back to "was
    # this posted by our bot" (app/teams/thread.py::is_placeholder_message).
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""

    # ── Google Threat Intelligence (GTI) Agentic API ────────────────────────────
    gti_api_key: str = ""
    gti_api_base_url: str = "https://www.virustotal.com/api/v3"
    # A push subscription's ack_deadline_seconds maxes out at 600s (10 min)
    # with no lease-renewal available to a push endpoint — see
    # terraform/main.tf's job_subscription and the README's "Timeout budget"
    # section. This default leaves ~120s of headroom under that hard ceiling
    # for attachment downloads, thread-context fetch, delivery, and cold
    # start. Raising this only makes sense together with raising
    # ack_deadline_seconds and service_config.timeout_seconds in lockstep —
    # and 600s is a true platform ceiling, not a quota that can be lifted.
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
    # Bot config (output-format doc), per-thread GTI session documents, and
    # the redelivery dedup claims (app/dedup_store.py) all live in Firestore
    # — see app/output_format_store.py, app/gti/session_store.py.
    firestore_bot_config_collection: str = "gti-bot-config"
    firestore_output_format_doc: str = "gti-custom-output-format"

    # Deploy-time default used to seed the Firestore document on first read
    output_format_instructions: str = ""

    # ── Microsoft Graph (channel thread context) ──────────────────────────────
    # Requires the CLIENT_ID app registration to be granted the application
    # permission ChannelMessage.Read.All with tenant-admin consent. Channel-only —
    # Teams has no equivalent thread concept for personal/group chats.
    thread_context_enabled: bool = True
    thread_context_message_count: int = 5

    # ── Job hand-off from the Ingest Cloud Function ─────────────────────────────
    # No topic/subscription name lives here (unlike Azure's job_queue_name) —
    # GCP's push model routes entirely via the Pub/Sub subscription's
    # push_endpoint URL, configured once in terraform/main.tf. This function
    # never needs to know the topic or subscription's name to do its job.
    #
    # A push job older than this (seconds, measured from the job's own
    # `enqueuedAt` field — NOT Pub/Sub's message.publishTime, to keep this
    # check identical to azure-queue's and independent of any Pub/Sub
    # redelivery) is treated as stale — the placeholder is edited to say so
    # and no GTI call is made.
    max_job_age_seconds: float = 480.0

    # ── HTTP connection pool sizing ───────────────────────────────────────────
    # Reuses the THREADS env var terraform/main.tf sets from
    # var.worker_concurrency (Cloud Run's max_instance_request_concurrency)
    # so every outbound HTTP client's connection pool (Bot Framework
    # Connector, GTI, Graph) can hold one connection per concurrent request
    # this instance is actually given, instead of urllib3's default pool
    # size of 10.
    concurrent_requests: int = Field(default=15, validation_alias="THREADS")

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

    @field_validator("thread_context_message_count")
    @classmethod
    def clamp_thread_context_message_count(cls, v: int) -> int:
        """Clamp the thread context message count between 1 and 30."""
        return max(1, min(v, 30))


settings = Settings()
