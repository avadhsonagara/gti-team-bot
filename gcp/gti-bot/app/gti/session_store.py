"""
Persists the mapping from a Teams thread (Post ID) or conversation to its
GTI Agentic session_id (plus the Team ID it belongs to, for channel threads),
so a second message in the same thread continues the same GTI session
instead of starting a fresh one each time.

Google Cloud Firestore-backed — one document per session key, in the same
`firestore_bot_config_collection` app/output_format_store.py already uses
for the output-format doc (default "gti-bot-config") — session keys are
Teams conversation/thread-post IDs, which won't collide with that doc's
fixed ID ("gti-custom-output-format"). Firestore document IDs may not
contain "/"; _doc_id() substitutes it out since Teams conversation IDs
don't otherwise collide once that one character is swapped.

Schema per document: {"session_id": "<gti_session_id>", "team_id": "<team_id_or_None>"}
"""
import logging
from typing import Optional

from google.cloud import firestore

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_firestore_client: Optional[firestore.AsyncClient] = None


def _get_firestore_client(cfg: Settings) -> Optional[firestore.AsyncClient]:
    """Return or initialize the singleton async Firestore client."""
    global _firestore_client
    if _firestore_client is None:
        try:
            _firestore_client = firestore.AsyncClient(
                project=cfg.gcp_project_id or None,
                database=cfg.firestore_database or "(default)",
            )
        except Exception as exc:
            logger.warning("[SESSION] Failed to initialize Firestore client (%s).", exc)
            return None
    return _firestore_client


def _doc_id(key: str) -> str:
    """Sanitize a session key into a valid Firestore document ID ('/' isn't allowed)."""
    return key.replace("/", "_")


async def get_session_id(key: str) -> Optional[str]:
    """Return the stored GTI session_id for this key (Post ID or Conversation ID), or None."""
    if not key:
        return None
    client = _get_firestore_client(settings)
    if client is None:
        return None

    try:
        doc = await client.collection(settings.firestore_bot_config_collection).document(_doc_id(key)).get()
        if not doc.exists:
            return None
        session_id = (doc.to_dict() or {}).get("session_id")
        if session_id:
            logger.info("[SESSION] Found existing session_id=%s for key=%s", session_id, key)
        return session_id
    except Exception as exc:
        logger.warning("[SESSION] Failed to read session for key=%s (%s).", key, exc)
        return None


async def get_team_id(key: str) -> Optional[str]:
    """Return the stored Team ID for this key, or None."""
    if not key:
        return None
    client = _get_firestore_client(settings)
    if client is None:
        return None

    try:
        doc = await client.collection(settings.firestore_bot_config_collection).document(_doc_id(key)).get()
        if not doc.exists:
            return None
        return (doc.to_dict() or {}).get("team_id")
    except Exception as exc:
        logger.warning("[SESSION] Failed to read team_id for key=%s (%s).", key, exc)
        return None


async def set_session_id(key: str, session_id: str, team_id: str | None = None) -> None:
    """Persist the GTI session_id (and Team ID, for channel threads) for this key."""
    if not key or not session_id:
        return
    client = _get_firestore_client(settings)
    if client is None:
        return

    try:
        await client.collection(settings.firestore_bot_config_collection).document(_doc_id(key)).set({
            "session_id": session_id,
            "team_id": team_id or None,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        logger.info("[SESSION] Stored session_id=%s team_id=%s for key=%s", session_id, team_id or "-", key)
    except Exception as exc:
        logger.error("[SESSION] Failed to write session for key=%s (%s).", key, exc)
