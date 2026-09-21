"""
Persists the mapping from a Teams channel thread to its GTI Agentic
session_id, so a second message in the same thread continues the same GTI
session instead of starting a fresh one each time.

Firestore-backed — one document per thread, in the same
`firestore_bot_config_collection` app/output_format_store.py also uses.
Document ids are namespaced by channel (falling back to team, see
_doc_id()), since Teams message/Post IDs are only unique within the
channel that created them and could otherwise collide across channels.

Schema per document:
  {"session_id": "<gti_session_id>", "team_id": "<team_id>", "channel_id": "<channel_id>"}
"""
import logging
import threading
from typing import Optional

from google.cloud import firestore

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_firestore_client: Optional[firestore.Client] = None

# Per-thread locks so two concurrent requests for the SAME channel thread
# serialize around read-session -> maybe-create-in-GTI -> write-session,
# instead of both reading "no session yet", both creating a GTI session, and
# one write silently orphaning the other.
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()

# Separate document namespace, same collection: caches the last-known
# team_id for a channel, since channelData.team isn't always present on an
# activity but Graph's channel-message APIs require team_id in the URL.
_CHANNEL_TEAM_PREFIX = "channel_team_map"


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
            logger.warning("[SESSION] Failed to initialize Firestore client (%s).", exc)
            return None
    return _firestore_client


def _sanitize(value: str) -> str:
    """Replace characters that aren't safe in a Firestore document ID ('/' isn't allowed)."""
    return (value or "").replace("/", "_")


def _doc_id(team_id: str, channel_id: str, key: str) -> str:
    """Namespace a session key by its channel (falling back to team) to avoid cross-channel collisions."""
    scope = _sanitize(channel_id or team_id or "-")
    return f"{scope}::{_sanitize(key)}"


def session_lock(team_id: str, channel_id: str, key: str) -> threading.Lock:
    """
    Return a process-wide lock scoped to this team/channel's session key.
    Hold it around the read-session -> maybe-create-in-GTI -> write-session
    sequence so two concurrent requests for the same thread can't both
    create a GTI session and silently orphan one.
    """
    lock_key = _doc_id(team_id, channel_id, key)
    with _session_locks_guard:
        lock = _session_locks.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _session_locks[lock_key] = lock
        return lock


def get_session_id(team_id: str, channel_id: str, key: str) -> Optional[str]:
    """Return the stored GTI session_id for this team/channel's thread key, or None."""
    if not key:
        return None
    client = _get_firestore_client(settings)
    if client is None:
        return None

    try:
        doc = client.collection(settings.firestore_bot_config_collection).document(
            _doc_id(team_id, channel_id, key)
        ).get()
        if not doc.exists:
            return None
        session_id = (doc.to_dict() or {}).get("session_id")
        if session_id:
            logger.info(
                "[SESSION] Found existing session_id=%s for team=%s channel=%s key=%s",
                session_id, team_id or "-", channel_id or "-", key,
            )
        return session_id
    except Exception as exc:
        logger.warning(
            "[SESSION] Failed to read session for team=%s channel=%s key=%s (%s).",
            team_id, channel_id, key, exc,
        )
        return None


def set_session_id(team_id: str, channel_id: str, key: str, session_id: str) -> None:
    """Persist the GTI session_id for this team/channel's thread key."""
    if not key or not session_id:
        return
    client = _get_firestore_client(settings)
    if client is None:
        return

    try:
        client.collection(settings.firestore_bot_config_collection).document(
            _doc_id(team_id, channel_id, key)
        ).set({
            "session_id": session_id,
            "team_id": team_id or "",
            "channel_id": channel_id or "",
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        logger.info(
            "[SESSION] Stored session_id=%s for team=%s channel=%s key=%s",
            session_id, team_id or "-", channel_id or "-", key,
        )
    except Exception as exc:
        logger.error(
            "[SESSION] Failed to write session for team=%s channel=%s key=%s (%s).",
            team_id, channel_id, key, exc,
        )

    if team_id and channel_id:
        _remember_channel_team(channel_id, team_id)


def _remember_channel_team(channel_id: str, team_id: str) -> None:
    """Record channel_id -> team_id so a later activity missing channelData.team can still be resolved."""
    client = _get_firestore_client(settings)
    if client is None:
        return
    try:
        client.collection(settings.firestore_bot_config_collection).document(
            f"{_CHANNEL_TEAM_PREFIX}::{_sanitize(channel_id)}"
        ).set({"team_id": team_id}, merge=True)
    except Exception as exc:
        logger.warning("[SESSION] Failed to cache team_id for channel=%s (%s).", channel_id, exc)


def delete_team_sessions(team_id: str) -> None:
    """Delete every stored session and cached channel mapping for a team."""
    if not team_id:
        return
    client = _get_firestore_client(settings)
    if client is None:
        return
    try:
        collection = client.collection(settings.firestore_bot_config_collection)
        docs = collection.where(filter=firestore.FieldFilter("team_id", "==", team_id)).stream()
        deleted = 0
        for doc in docs:
            doc.reference.delete()
            deleted += 1
        logger.info("[SESSION] Deleted %d Firestore document(s) for team=%s", deleted, team_id)
    except Exception as exc:
        logger.error("[SESSION] Failed to delete sessions for team=%s (%s).", team_id, exc)


def get_team_id_for_channel(channel_id: str) -> Optional[str]:
    """
    Best-effort lookup of the last known team_id for a channel, keyed
    independently of any specific thread. Used as a fallback when the
    current activity's own channelData.team is missing.
    """
    if not channel_id:
        return None
    client = _get_firestore_client(settings)
    if client is None:
        return None
    try:
        doc = client.collection(settings.firestore_bot_config_collection).document(
            f"{_CHANNEL_TEAM_PREFIX}::{_sanitize(channel_id)}"
        ).get()
        if not doc.exists:
            return None
        return (doc.to_dict() or {}).get("team_id") or None
    except Exception as exc:
        logger.warning("[SESSION] Failed to read cached team_id for channel=%s (%s).", channel_id, exc)
        return None
