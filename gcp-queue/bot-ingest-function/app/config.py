"""
Single source of truth for runtime configuration on the Ingest Cloud Function.

Reads values from environment variables (and .env at startup). Field names
map to env vars via automatic uppercasing: e.g. `client_id` reads from
`CLIENT_ID`.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import DEFAULT_TOPIC_NAME


class Settings(BaseSettings):
    """Configuration settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── Microsoft Teams / Bot Framework ─────────────────────────────────────────
    # Must be the SAME app registration bot-worker-function/.env uses — both
    # Cloud Functions authenticate as one bot. client_secret is required here
    # (not just on the worker) because this function posts the placeholder
    # and any error notices via app/teams/bot_client.py::get_bot_token(),
    # which needs client_id + client_secret + tenant_id to mint an outbound
    # Bot Framework Connector token.
    client_id: str = ""
    client_secret: str = ""
    tenant_id: str = ""

    # ── Pub/Sub hand-off to the Worker Cloud Function ───────────────────────────
    # This function only ever publishes to it (app/queue_job.py); the actual
    # topic resource, its push subscription, and the worker's push identity
    # are all provisioned in terraform/main.tf — nothing here grants access,
    # this is just which topic to call PublisherClient.publish() against.
    gcp_project_id: str = ""
    pubsub_topic: str = DEFAULT_TOPIC_NAME

    # ── HTTP connection pool sizing ───────────────────────────────────────────
    # Reuses the THREADS env var terraform/main.tf sets from
    # var.ingest_concurrency (Cloud Run's max_instance_request_concurrency)
    # so app/teams/bot_client.py's connection pool can hold one connection
    # per concurrent request this instance is actually given, instead of
    # urllib3's default pool size of 10.
    concurrent_requests: int = Field(default=20, validation_alias="THREADS")

    # ── Safety guards ─────────────────────────────────────────────────────────
    # Pub/Sub messages can be up to 10 MB — far more generous than Azure
    # Storage Queue's hard 64 KiB cap that motivated this same guard on
    # Azure. Kept at a similar magnitude anyway as a deliberate app-level
    # guard (not a platform ceiling): the enqueued job is the raw Activity
    # JSON plus a little metadata, almost always tiny, and there's no reason
    # to accept and forward a pathologically large one just because the
    # platform technically allows it — better to fail fast with a clean
    # "too large" message to the user than let a huge payload balloon
    # downstream (Pub/Sub cost, worker processing, Firestore writes).
    max_job_payload_bytes: int = 48 * 1024
    # This endpoint is publicly reachable (Cloud Run ingress ALLOW_ALL — the
    # bot's own Bearer-token check is what actually gates it), so it will get
    # arbitrary/abusive traffic. Reject an implausibly large body outright,
    # before spending any CPU parsing it as JSON.
    max_request_body_bytes: int = 1 * 1024 * 1024


settings = Settings()
