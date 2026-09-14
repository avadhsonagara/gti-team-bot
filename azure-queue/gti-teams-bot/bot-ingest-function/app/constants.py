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
