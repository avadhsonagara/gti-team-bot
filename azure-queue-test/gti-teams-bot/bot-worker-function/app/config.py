"""
Single source of truth for runtime configuration — Worker Function.

Reads values from environment variables (and .env at startup). Azure
Function App Application Settings are exposed as environment variables at
runtime, so no Azure-specific config loading is needed.
Field names map to env vars via automatic uppercasing:
  e.g. `client_id` reads from `CLIENT_ID`.
"""
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import DEFAULT_QUEUE_NAME


class Settings(BaseSettings):
    """Configuration settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── Microsoft Teams / Bot Framework ──────────────────────────────────────
    # Same User-Assigned Managed Identity as the ingest function (must be the
    # SAME identity — both apps authenticate as one bot): edits/deletes the
    # placeholder and sends the final reply (app/teams/bot_client.py), and is
    # also this app's own identity check when matching a channel thread
    # message back to "was this posted by our bot" (app/teams/thread.py).
    client_id: str = ""
    managed_identity_client_id: str = ""

    # ── Google Threat Intelligence (GTI) Agentic API ─────────────────────────
    gti_api_key: str = ""
    gti_api_base_url: str = "https://www.virustotal.com/api/v3"
    # app/gti/client.py in THIS copy is a performance-test stub that makes no
    # real GTI call — it just sleeps for this many seconds (simulating GTI's
    # real processing latency) before returning a fixed canned response.
    # Fixed at 180s for this test harness. Still comfortably under host.json's
    # functionTimeout (9m30s) with headroom for attachment downloads,
    # thread-context fetch, and delivery either side of it.
    gti_timeout_seconds: float = 180.0

    # ── Output format instructions ───────────────────────────────────────────
    # Deploy-time default, used to seed the JSON config blob on first read —
    # see app/output_format_store.py.
    output_format_instructions: str = ""
    azure_web_jobs_storage: str = Field(default="", validation_alias="AzureWebJobsStorage")

    # ── Microsoft Graph (channel thread context) ─────────────────────────────
    # Requires the Managed Identity above to be granted the Graph APPLICATION
    # permission ChannelMessage.Read.All with tenant-admin consent.
    # Channel-only — Teams has no equivalent thread concept for personal/
    # group chats.
    thread_context_enabled: bool = True
    # Clamped to [1, 30] regardless of what's configured — see
    # clamp_thread_context_message_count() below (bicep/main.bicep's own
    # threadContextMessageCount parameter enforces the same [1, 30] range at
    # deploy time, so this is a defense-in-depth floor, not the only guard).
    # Higher values grow the prompt sent to GTI (more prior messages
    # included), with no gain in Graph API calls either way (the whole
    # thread is always fetched once, this only controls how much of it gets
    # used).
    thread_context_message_count: int = 5

    # ── Queue hand-off from the Ingest Function App ──────────────────────────
    job_queue_name: str = DEFAULT_QUEUE_NAME
    # A message that has sat in the queue longer than this before being
    # dequeued is treated as stale — a large backlog, or a message that was
    # retried after a transient failure and has now aged out its usefulness —
    # rather than run an expensive multi-minute GTI query for a request that
    # old, the placeholder is edited to say so and no GTI call is made.
    max_job_age_seconds: float = 480.0

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

    @field_validator("thread_context_message_count")
    @classmethod
    def clamp_thread_context_message_count(cls, v: int) -> int:
        """Cap at 30 and floor at 1 regardless of what's configured — a
        misconfigured app setting can't blow up the prompt sent to GTI."""
        return max(1, min(v, 30))


settings = Settings()
