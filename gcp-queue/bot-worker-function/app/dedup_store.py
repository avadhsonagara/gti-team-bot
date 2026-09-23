"""
Cross-instance redelivery guard for Pub/Sub push jobs.

Pub/Sub push is at-least-once with no lease-renewal available to a push
endpoint (unlike Azure Storage Queue's automatic peek-lock, which keeps a
message invisible for as long as the processing instance stays healthy) — if
this worker doesn't answer within the subscription's ack_deadline_seconds,
Pub/Sub redelivers the same message, possibly to a second, concurrent
instance while the first is still running. An in-memory guard (like
gcp/gcp-bot-function's `_claim_activity`) doesn't help here: Cloud Functions
gen2 can scale to multiple instances with independent memory. Firestore is
already a hard dependency (session_store.py, output_format_store.py), so a
`create()`-based claim — which raises AlreadyExists if another instance
already claimed the same Pub/Sub messageId — is a minimal way to close this
gap without new infrastructure.

Configure a Firestore TTL policy on this collection's `claimed_at` field so
old claims expire on their own rather than accumulating forever.
"""
import logging
from typing import Optional

from google.api_core import exceptions as gcloud_exceptions
from google.cloud import firestore

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_firestore_client: Optional[firestore.Client] = None

# Separate document namespace, same collection session_store.py and
# output_format_store.py already use — no new Firestore collection to
# provision or grant access to.
_DEDUP_PREFIX = "message_dedup"


def _get_firestore_client(cfg: Settings) -> Optional[firestore.Client]:
    """Return or initialize the singleton Firestore client."""
    global _firestore_client
    if _firestore_client is None:
        try:
            _firestore_client = firestore.Client(
                project=cfg.gcp_project_id or None,
                database=cfg.firestore_database or "(default)",
            )
        except Exception as exc:
            logger.warning("[DEDUP] Failed to initialize Firestore client (%s).", exc)
            return None
    return _firestore_client


def claim_message(message_id: str) -> bool:
    """
    Attempt to claim a Pub/Sub messageId as "being processed by this
    invocation". Returns True if this is the first delivery to claim it,
    False if another delivery (this instance or a concurrent one) already
    holds the claim.

    Fails open (returns True) if Firestore is unreachable — a missed
    dedup check is far less harmful than dropping a real job because a
    transient Firestore error looked like a duplicate.
    """
    if not message_id:
        # Nothing stable to key a claim on (shouldn't happen for a real
        # Pub/Sub delivery) — let it proceed rather than block on nothing.
        return True

    client = _get_firestore_client(settings)
    if client is None:
        return True

    doc_ref = client.collection(settings.firestore_bot_config_collection).document(f"{_DEDUP_PREFIX}::{message_id}")
    try:
        doc_ref.create({"claimed_at": firestore.SERVER_TIMESTAMP})
        return True
    except gcloud_exceptions.AlreadyExists:
        logger.warning("[DEDUP] messageId=%s already claimed by another delivery — skipping duplicate processing.", message_id)
        return False
    except Exception as exc:
        logger.warning("[DEDUP] Failed to claim messageId=%s (%s) — proceeding without a dedup guarantee.", message_id, exc)
        return True
