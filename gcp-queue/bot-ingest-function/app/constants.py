"""Application constants for the GTI Teams Bot Ingest Function."""
APP_NAME = "gti-teams-bot-ingest"


# Kept byte-for-byte identical to bot-worker-function/app/constants.py — the
# two Cloud Functions are deployed and versioned independently (no shared
# package), but must agree on this exact string. This function posts it as
# the placeholder message body; the worker's channel thread-context fetch
# (bot-worker-function/app/teams/thread.py, is_placeholder_message) matches
# on it to exclude that placeholder from what gets fed back into the GTI
# prompt as prior conversation history.
PLACEHOLDER_TEXT = "⏳ Looking into that …"

# Name of the Pub/Sub topic used to hand a parsed activity off to the Worker
# Cloud Function. Kept identical to bot-worker-function/app/constants.py for
# the same reason as PLACEHOLDER_TEXT above — the actual topic resource name
# is set in Terraform (terraform/main.tf), this is just the app-level default
# read from PUBSUB_TOPIC in app/config.py.
DEFAULT_TOPIC_NAME = "gti-query-jobs"


# =============================================================================
# Outbound Bot Framework Connector API (app/teams/bot_client.py) — posting,
# editing, and deleting the "looking into that…" placeholder message.
# =============================================================================

# (connect, read) timeout seconds for every Connector API call.
BOT_CONNECTOR_TIMEOUT = (10.0, 30.0)

# Retry policy for idempotent Connector calls (PUT/DELETE — see
# app/teams/bot_client.py's own comment on why POST is deliberately excluded).
BOT_CONNECTOR_RETRY_TOTAL = 3
BOT_CONNECTOR_RETRY_BACKOFF_FACTOR = 0.5
BOT_CONNECTOR_RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)
