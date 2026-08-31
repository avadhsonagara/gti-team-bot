"""
Incremental cursor persistence using Google Cloud Firestore.

Cloud Run functions instances are ephemeral and can scale to zero between
scheduler ticks, so the cursor cannot live on local disk. It is stored as a
Firestore document in the configured collection (default: `rs-alerts-state`, document: `cursor`).
"""
import logging
from typing import Optional

from google.cloud import firestore

from app.config import Settings

logger = logging.getLogger("rs-alerts")

_firestore_client: Optional[firestore.Client] = None


def _get_firestore_client(settings: Settings) -> firestore.Client:
    """Return or initialize the singleton Firestore client."""
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(
            project=settings.gcp_project_id or None,
            database=settings.firestore_database or "(default)",
        )
    return _firestore_client


def read_cursor(settings: Settings) -> str | None:
    """Read the incremental cursor (last seen audit.update_time) from Firestore."""
    try:
        client = _get_firestore_client(settings)
        doc_ref = client.collection(settings.firestore_state_collection).document(
            settings.firestore_state_doc
        )
        doc = doc_ref.get()
        if not doc.exists:
            return None

        data = doc.to_dict() or {}
        return data.get("last_update_time")
    except Exception as exc:
        logger.warning("Failed to read cursor from Firestore (%s) — starting fresh.", exc)
        return None


def write_cursor(settings: Settings, update_time: str) -> None:
    """Write the incremental cursor to Firestore."""
    try:
        client = _get_firestore_client(settings)
        doc_ref = client.collection(settings.firestore_state_collection).document(
            settings.firestore_state_doc
        )
        doc_ref.set({
            "last_update_time": update_time,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
    except Exception as exc:
        logger.error("Failed to write cursor to Firestore: %s", exc)
        raise
