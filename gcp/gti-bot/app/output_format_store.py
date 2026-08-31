"""
Output-format instructions persistence using Google Cloud Firestore.

Stores custom formatting instructions in a Firestore document (collection: `bot-config`,
document: `output-format`) as the durable source of truth.
Seeds an initial value from the deploy-time `OUTPUT_FORMAT_INSTRUCTIONS` configuration
on first read if not yet present in Firestore.
"""
import logging
from typing import Optional

from google.cloud import firestore

from app.config import Settings

logger = logging.getLogger("gti-teams-bot")

_firestore_client: Optional[firestore.Client] = None


def _get_firestore_client(settings: Settings) -> Optional[firestore.Client]:
    """Return or initialize the singleton Firestore client."""
    global _firestore_client
    if _firestore_client is None:
        try:
            _firestore_client = firestore.Client(
                project=settings.gcp_project_id or None,
                database=settings.firestore_database or "(default)",
            )
        except Exception as exc:
            logger.warning("[CONFIG] Failed to initialize Firestore client (%s). Using fallback settings.", exc)
            return None
    return _firestore_client


def write_output_format(settings: Settings, format_text: str) -> None:
    """Persist custom output-format instructions to Firestore."""
    client = _get_firestore_client(settings)
    if client is None:
        return

    try:
        doc_ref = client.collection(settings.firestore_bot_config_collection).document(
            settings.firestore_output_format_doc
        )
        doc_ref.set({
            "output_format": format_text,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        logger.info("[CONFIG] Output format updated in Firestore (%d chars).", len(format_text))
    except Exception as exc:
        logger.error("[CONFIG] Failed to write output format to Firestore: %s", exc)


def get_output_format(settings: Settings) -> str:
    """
    Return the current output-format instructions.

    Reads the Firestore document if present; otherwise seeds it from the
    deploy-time OUTPUT_FORMAT_INSTRUCTIONS default so later reads (and any
    future config tooling) have a durable source of truth instead of
    relying solely on environment variables.
    """
    client = _get_firestore_client(settings)
    if client is None:
        return settings.output_format_instructions

    try:
        doc_ref = client.collection(settings.firestore_bot_config_collection).document(
            settings.firestore_output_format_doc
        )
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            saved_format = data.get("output_format", "")
            return saved_format if saved_format else settings.output_format_instructions

        # Document does not exist yet; seed from deploy-time default
        default = settings.output_format_instructions
        if default:
            write_output_format(settings, default)
        return default

    except Exception as exc:
        logger.warning("[CONFIG] Failed to read output format from Firestore (%s) — using deploy-time default.", exc)
        return settings.output_format_instructions
