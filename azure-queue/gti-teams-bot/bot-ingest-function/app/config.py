"""
Runtime configuration settings for the GTI Teams Bot Ingest Function.

Loads application settings from environment variables and local .env files,
providing validated configuration for Microsoft Teams, Bot Framework, and Azure Storage Queue.
"""
from pydantic import Field
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
    # CLIENT_ID + MANAGED_IDENTITY_CLIENT_ID authenticate via the same
    # User-Assigned Managed Identity the Azure Bot resource uses as its App ID
    # (msaAppType "UserAssignedMSI") — no client secret, ever. Used to verify
    # inbound activities (app/teams/auth.py) and to post the "looking into
    # that…" placeholder via the Bot Framework Connector API
    # (app/teams/bot_client.py). Must be the SAME identity the Worker
    # Function App uses — both apps authenticate as one bot.
    client_id: str = ""
    managed_identity_client_id: str = ""
    # Bot Service's msaAppTenantId — optional, defense-in-depth. Widens the
    # accepted issuer claim on inbound activity tokens to also include a
    # tenant-scoped Entra issuer, matching a gap Microsoft's own SDK
    # acknowledges for some UserAssignedMSI/SingleTenant-hosted bots (see
    # app/teams/auth.py::_expected_issuers) — this bot's own real tokens carry
    # the classic issuer either way, so this is not required for auth to work.
    tenant_id: str = ""

    # ── Queue hand-off to the Worker Function App ────────────────────────────
    # Both Function Apps must point at the SAME underlying storage account
    # and agree on this queue name — this app only ever writes to it (via the
    # azure-storage-queue SDK directly, matching this codebase's existing
    # convention of using the storage SDKs directly rather than WebJobs
    # bindings — see app/gti/session_store.py and app/output_format_store.py
    # in bot-worker-function/ for the same pattern), the worker only ever
    # reads from it (via its native queue_trigger binding, which is what
    # gives the worker automatic poison-queue routing).
    azure_web_jobs_storage: str = Field(default="", validation_alias="AzureWebJobsStorage")
    job_queue_name: str = DEFAULT_QUEUE_NAME

    # ── HTTP connection pool sizing ───────────────────────────────────────────
    # Matches bicep/main.bicep's ingestConcurrentRequests (same value also
    # drives PYTHON_THREADPOOL_THREAD_COUNT) so app/teams/bot_client.py's
    # connection pool — shared byte-for-byte with bot-worker-function's own
    # copy, hence the generic field name — can hold one connection per
    # concurrent request instead of urllib3's default pool size of 10.
    concurrent_requests: int = 20

    # ── Safety guards ─────────────────────────────────────────────────────────
    # Azure Storage Queue messages are hard-capped at 64 KiB. The enqueued job
    # is the raw Activity JSON plus a little metadata — almost always tiny,
    # but a pathological activity (e.g. very large channelData) could exceed
    # it. Stay comfortably under the real ceiling so the failure mode is a
    # clean, user-visible "too large" message instead of a queue SDK error.
    max_job_payload_bytes: int = 48 * 1024
    # This endpoint is publicly reachable (auth_level=ANONYMOUS — the bot's
    # own Bearer-token check is what actually gates it), so it will get
    # arbitrary/abusive traffic. Reject an implausibly large body outright,
    # before spending any CPU parsing it as JSON.
    max_request_body_bytes: int = 1 * 1024 * 1024


settings = Settings()
