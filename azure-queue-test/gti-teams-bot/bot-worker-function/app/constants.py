from pathlib import Path

APP_NAME = "gti-teams-bot-worker"

# Kept byte-for-byte identical to bot-ingest-function/app/constants.py — see
# the comment there. The ingest function posts this text as the placeholder
# message body; is_placeholder_message() below (via app/teams/thread.py)
# matches on it to exclude the bot's own placeholder from channel thread
# context before it's fed back into the GTI prompt as prior history.
PLACEHOLDER_TEXT = "⏳ Looking into that with Google Threat Intelligence…"

# Name of the Storage Queue this Function App is triggered from. Kept
# identical to bot-ingest-function/app/constants.py for the same reason.
DEFAULT_QUEUE_NAME = "gti-query-jobs"

# Optional system instructions file loaded at startup
_PROMPT_PATH = Path(__file__).parent / "gti" / "prompt.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip() if _PROMPT_PATH.exists() else ""


# =============================================================================
# Google Threat Intelligence (GTI) Agentic API client (app/gti/client.py)
# =============================================================================
# NOTE: the GTI *read* timeout is NOT here — it's settings.gti_timeout_seconds
# (app/config.py), because it's deployment-configurable (GTI_TIMEOUT_SECONDS
# app setting) and part of the harmonized timeout chain with host.json's
# functionTimeout. Only the connect-timeout half and the retry/backoff knobs
# below are fixed, non-configurable constants.

# (connect, read) timeout tuple's connect half — how long to wait for the TCP
# connection itself before giving up; the read half is settings.gti_timeout_seconds.
GTI_CONNECT_TIMEOUT_SECONDS = 15.0

GTI_MAX_RETRIES = 3

# Base delay for exponential backoff on 5xx / connection / unexpected errors:
# attempt N waits GTI_RETRY_DELAY_SECONDS * 2**N seconds (+ jitter, below).
GTI_RETRY_DELAY_SECONDS = 2.0

# Base delay for the 429 (rate limit) backoff schedule, used when GTI doesn't
# send a Retry-After header — see GTI_RATE_LIMIT_BACKOFF_MULTIPLIERS.
GTI_RATE_LIMIT_RETRY_DELAY_SECONDS = 5.0

# GTI_RATE_LIMIT_RETRY_DELAY_SECONDS * each of these -> the 429 backoff
# schedule (10s/20s/30s at the default 5.0s delay above), one entry per retry
# attempt (capped at the last entry if there are more retries than entries).
GTI_RATE_LIMIT_BACKOFF_MULTIPLIERS = (2, 4, 6)

# Random jitter (seconds) added on top of every retry delay computed below —
# a 429 response's own Retry-After header, the 429 backoff schedule, and the
# 5xx / connection-error / unexpected-error exponential backoff — so
# concurrent retries from multiple instances don't all wake up and retry at
# the exact same instant. One shared range for all of them.
GTI_JITTER_RANGE = (0.5, 1.5)


# =============================================================================
# Outbound tokens — Bot Framework Connector (app/teams/bot_client.py) and
# Microsoft Graph (app/graph/client.py)
# =============================================================================

# Refresh a cached app-only access token this many seconds before it actually
# expires, so a request never starts with a token that expires mid-flight.
TOKEN_EXPIRY_SAFETY_SECONDS = 60


# =============================================================================
# Outbound Bot Framework Connector API (app/teams/bot_client.py) — sending,
# updating, and deleting a Teams message (the "looking into that…" placeholder
# and the final response).
# =============================================================================

# (connect, read) timeout seconds for every Connector API call.
BOT_CONNECTOR_TIMEOUT = (10.0, 30.0)

# Retry policy for idempotent Connector calls (PUT/DELETE — see
# app/teams/bot_client.py's own comment on why POST is deliberately excluded).
BOT_CONNECTOR_RETRY_TOTAL = 3
BOT_CONNECTOR_RETRY_BACKOFF_FACTOR = 0.5
BOT_CONNECTOR_RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)


# =============================================================================
# Microsoft Graph client (app/graph/client.py)
# =============================================================================
# Every call to graph.microsoft.com goes through GraphClient.get() and uses
# this single timeout (applied to both the connect and read phases, since
# it's passed to `requests` as one scalar rather than a (connect, read)
# tuple) unless a caller explicitly overrides it — app/teams/thread.py's
# fetch_thread_messages() and app/teams/attachments.py's Graph message-list/
# share-download calls all rely on this default.

GRAPH_API_TIMEOUT_SECONDS = 30.0


# =============================================================================
# Attachment downloads (app/teams/attachments.py) — Bot Framework Connector
# downloadUrl/content_url for personal/group chats, Microsoft Graph fallback
# for channel (and group chat's existing-file-share case), since Bot
# Framework never includes a usable content_url on a channel message at all
# — confirmed live, even for a freshly-uploaded file, not just an existing
# SharePoint/OneDrive share.
# =============================================================================

# (connect, read) timeout seconds for downloading one attachment's bytes.
ATTACHMENT_DOWNLOAD_TIMEOUT = (10.0, 30.0)

# Safety ceiling on paginated Graph message-list fetches (channel replies, or
# a group chat's recent messages) when locating the inbound message's real
# attachment reference — NOT the primary stopping condition. That's
# timestamp-based (see attachments.py's _page_reaches_target/target_timestamp): the
# actual target message's own timestamp, so a very high-traffic channel
# still gets found correctly no matter how many pages that takes. This cap
# only guards against truly runaway pagination (e.g. a broken nextLink, or
# a badly-skewed clock making the timestamp check never trigger) — set high
# enough that it should never realistically be hit in ordinary use.
ATTACHMENT_GRAPH_MESSAGE_LIST_MAX_PAGES = 50


# =============================================================================
# Channel thread context (app/teams/thread.py)
# =============================================================================

# Safety ceiling on paginated Graph /replies fetches when building thread
# context — NOT the primary stopping condition. That's timestamp-based (see
# thread.py's _page_reaches_target/target_timestamp): the triggering
# activity's own timestamp, so a long-running thread still gets its truly
# most recent messages no matter how many pages that takes. This cap only
# guards against truly runaway pagination — set high enough that it should
# never realistically be hit in ordinary use.
THREAD_CONTEXT_MAX_PAGES = 50


# =============================================================================
# Output-format instructions cache (app/output_format_store.py)
# =============================================================================

# How long a read of the output-format instructions is cached in memory
# before the next call re-reads Blob Storage. The value changes rarely, so
# this avoids a storage round-trip on every single message.
OUTPUT_FORMAT_CACHE_TTL_SECONDS = 3600.0
