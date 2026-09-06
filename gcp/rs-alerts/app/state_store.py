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
    """Read the incremental cursor (last seen audit.update_time) from Firestore.

    Only a genuinely absent document is treated as "no prior cursor" (first
    run). Any other failure (permission error, network issue, outage) is
    left to propagate and abort the run, rather than being silently treated
    as a fresh start — which would re-fetch and re-send the entire alert
    history as a duplicate flood.
    """
    client = _get_firestore_client(settings)
    doc_ref = client.collection(settings.firestore_state_collection).document(
        settings.firestore_state_doc
    )
    doc = doc_ref.get()
    if not doc.exists:
        return None

    data = doc.to_dict() or {}
    return data.get("last_update_time")


def write_cursor(settings: Settings, update_time: str) -> None:
    """Write the incremental cursor to Firestore.

    The checkpoint is written after the alert has already been delivered to
    Teams, so a transient failure here (rather than a genuine one) would
    otherwise cause that alert to be re-sent on the next run. Retry with
    backoff to close most of that window before giving up and propagating.
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
            return
        except Exception as exc:
            if attempt == _CHECKPOINT_WRITE_RETRIES - 1:
                logger.error("Failed to write cursor to Firestore: %s", exc)
                raise
            delay = _CHECKPOINT_WRITE_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "Checkpoint write failed (attempt %d/%d): %s — retrying in %.1fs.",
                attempt + 1, _CHECKPOINT_WRITE_RETRIES, exc, delay,
            )
            time.sleep(delay)
