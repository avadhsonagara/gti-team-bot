"""
Output-format instructions persistence using a local JSON file (see
app/local_store.py) — no Google Cloud Firestore/project dependency needed
for local development.

Seeds an initial value from the deploy-time `OUTPUT_FORMAT_INSTRUCTIONS`
configuration on first read if not yet present in the local store.
"""
import logging

from app.config import Settings
from app.local_store import read_store, update_store

logger = logging.getLogger("gti-teams-bot")


async def write_output_format(settings: Settings, format_text: str) -> None:
    """Persist custom output-format instructions to the local JSON store."""
    try:
        def _apply(data: dict) -> None:
            data["output_format"] = format_text

        await update_store(settings.local_store_path, _apply)
        logger.info("[CONFIG] Output format updated in local store (%d chars).", len(format_text))
    except Exception as exc:
        logger.error("[CONFIG] Failed to write output format to local store: %s", exc)


async def get_output_format(settings: Settings) -> str:
    """
    Return the current output-format instructions.

    Reads the local JSON store if present; otherwise seeds it from the
    deploy-time OUTPUT_FORMAT_INSTRUCTIONS default so later reads have a
    durable source of truth instead of relying solely on environment variables.
    """
    try:
        data = await read_store(settings.local_store_path)
        saved_format = data.get("output_format", "")
        if saved_format:
            return saved_format

        # Not yet present; seed from deploy-time default
        default = settings.output_format_instructions
        if default:
            await write_output_format(settings, default)
        return default

    except Exception as exc:
        logger.warning("[CONFIG] Failed to read output format from local store (%s) — using deploy-time default.", exc)
        return settings.output_format_instructions
