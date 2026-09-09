"""
Persists the mapping from a Teams thread (Post ID) or conversation to its
GTI Agentic session_id (plus the Team ID it belongs to, for channel threads),
so a second message in the same thread continues the same GTI session
instead of starting a fresh one each time.

Local JSON-file-backed (see app/local_store.py) — no Google Cloud Firestore
dependency, for local development. Session keys are Teams conversation/
thread-post IDs.
"""
import logging
from typing import Optional

from app.config import settings
from app.local_store import read_store, update_store

logger = logging.getLogger("gti-teams-bot")


async def get_session_id(key: str) -> Optional[str]:
    """Return the stored GTI session_id for this key (Post ID or Conversation ID), or None."""
    if not key:
        return None
    try:
        data = await read_store(settings.local_store_path)
        session_id = (data.get("sessions", {}).get(key) or {}).get("session_id")
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
    try:
        data = await read_store(settings.local_store_path)
        return (data.get("sessions", {}).get(key) or {}).get("team_id")
    except Exception as exc:
        logger.warning("[SESSION] Failed to read team_id for key=%s (%s).", key, exc)
        return None


async def set_session_id(key: str, session_id: str, team_id: Optional[str] = None) -> None:
    """Persist the GTI session_id (and Team ID, for channel threads) for this key."""
    if not key or not session_id:
        return
    try:
        def _apply(data: dict) -> None:
            data.setdefault("sessions", {})[key] = {"session_id": session_id, "team_id": team_id or None}

        await update_store(settings.local_store_path, _apply)
        logger.info("[SESSION] Stored session_id=%s team_id=%s for key=%s", session_id, team_id or "-", key)
    except Exception as exc:
        logger.error("[SESSION] Failed to write session for key=%s (%s).", key, exc)
