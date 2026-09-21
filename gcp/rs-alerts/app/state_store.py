"""
Incremental cursor persistence using Google Cloud Firestore.

Cloud Run functions instances are ephemeral and can scale to zero between
scheduler ticks, so the cursor cannot live on local disk. It is stored as a
Firestore document in the configured collection (default: `rs-alerts-state`, document: `cursor`).
"""
import logging
import time
from typing import Optional

from google.cloud import firestore

from app.config import Settings

logger = logging.getLogger("rs-alerts")

_CHECKPOINT_WRITE_RETRIES = 3
_CHECKPOINT_WRITE_BACKOFF_SECONDS = 1.0

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
    """
    Read the incremental cursor (last-seen audit.update_time) from Firestore.

    Returns None if no cursor exists yet (first run). Any other read
    failure is left to propagate rather than treated as a fresh start,
    which would re-send the entire alert history as a duplicate flood.
    """
    client = _get_firestore_client(settings)
    doc_ref = client.collection(settings.firestore_state_collection).document(
        settings.firestore_state_doc
    )
    doc = doc_ref.get()
    if not doc.exists:
        logger.info("[RS-ALERTS CHECKPOINT] No previous cursor document found; initializing new cursor.")
        return None

    data = doc.to_dict() or {}
    cursor = data.get("last_update_time")
    if cursor:
        logger.info("[RS-ALERTS CHECKPOINT] Loaded existing cursor timestamp: %s", cursor)
    else:
        logger.info("[RS-ALERTS CHECKPOINT] Cursor document contained no timestamp; starting fresh.")
    return cursor


def write_cursor(settings: Settings, update_time: str) -> None:
    """
    Write the incremental cursor to Firestore, retrying transient failures
    with backoff. If all retries are exhausted after the Teams send already
    succeeded, that alert may be re-sent on the next run — an accepted,
    bounded residual risk.
    """
    client = _get_firestore_client(settings)
    doc_ref = client.collection(settings.firestore_state_collection).document(
        settings.firestore_state_doc
    )

    for attempt in range(_CHECKPOINT_WRITE_RETRIES):
        try:
            doc_ref.set({
                "last_update_time": update_time,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
            logger.info("[RS-ALERTS CHECKPOINT] Saved cursor timestamp %s to Firestore.", update_time)
            return
        except Exception as exc:
            if attempt == _CHECKPOINT_WRITE_RETRIES - 1:
                logger.error("[RS-ALERTS CHECKPOINT] Failed to write cursor to Firestore: %s", exc)
                raise
            delay = _CHECKPOINT_WRITE_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "[RS-ALERTS RETRY] Checkpoint write failed (attempt %d/%d) — retrying in %.1fs.",
                attempt + 1, _CHECKPOINT_WRITE_RETRIES, delay,
            )
            time.sleep(delay)
