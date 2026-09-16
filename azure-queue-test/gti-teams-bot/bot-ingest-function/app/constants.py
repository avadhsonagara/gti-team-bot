APP_NAME = "gti-teams-bot-ingest"

# Kept byte-for-byte identical to bot-worker-function/app/constants.py — the
# two Function Apps are deployed and versioned independently (no shared
# package), but must agree on this exact string. This function posts it as
# the placeholder message body; the worker's channel thread-context fetch
# (bot-worker-function/app/teams/thread.py, is_placeholder_message) matches
# on it to exclude that placeholder from what gets fed back into the GTI
# prompt as prior conversation history.
PLACEHOLDER_TEXT = "⏳ Looking into that with Google Threat Intelligence…"

# Name of the Storage Queue used to hand a parsed activity off to the Worker
# Function App. Kept identical to bot-worker-function/app/constants.py for
# the same reason as PLACEHOLDER_TEXT above.
DEFAULT_QUEUE_NAME = "gti-query-jobs"


# =============================================================================
# Inbound Bot Framework request authentication (app/teams/auth.py)
# =============================================================================

# Allowed clock skew (seconds) when validating an inbound JWT's exp/iat
# claims — matches Bot Framework's own documented clock-skew tolerance.
JWT_LEEWAY_SECONDS = 300


# =============================================================================
# Outbound Bot Framework Connector API (app/teams/bot_client.py) — posting,
# editing, and deleting the "looking into that…" placeholder message.
# =============================================================================

# Refresh a cached app-only Connector token this many seconds before it
# actually expires, so a request never starts with a token that expires
# mid-flight.
TOKEN_EXPIRY_SAFETY_SECONDS = 60

# (connect, read) timeout seconds for every Connector API call.
BOT_CONNECTOR_TIMEOUT = (10.0, 30.0)

# Retry policy for idempotent Connector calls (PUT/DELETE — see
# app/teams/bot_client.py's own comment on why POST is deliberately excluded).
BOT_CONNECTOR_RETRY_TOTAL = 3
BOT_CONNECTOR_RETRY_BACKOFF_FACTOR = 0.5
BOT_CONNECTOR_RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)
