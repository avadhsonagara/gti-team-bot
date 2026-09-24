from pathlib import Path

# ── Bot Framework Connector (app/teams/bot_client.py) ────────────────────────
BOT_CONNECTOR_TIMEOUT = (10.0, 30.0)
BOT_CONNECTOR_RETRY_TOTAL = 3
BOT_CONNECTOR_RETRY_BACKOFF_FACTOR = 0.5
BOT_CONNECTOR_RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

# Optional system instructions file loaded at startup
_PROMPT_PATH = Path(__file__).parent / "gti" / "prompt.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip() if _PROMPT_PATH.exists() else ""
