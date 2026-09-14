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
