"""
Persists the mapping from a Teams thread (Post ID) or conversation to its
GTI Agentic session_id (plus the Team ID it belongs to, for channel threads),
so a second message in the same thread continues the same GTI session
instead of starting a fresh one each time.

Azure Table Storage-backed — one entity (row) per session key, in the
Function App's own storage account (AzureWebJobsStorage). `upsert_entity` is
atomic per partition/row key, so two threads writing different session keys
concurrently can never clobber each other's entry — unlike a single shared
JSON blob (the previous design), where a full read-modify-write-whole-file
cycle let a second thread's write silently erase a first thread's just-added
key. This mirrors GCP's one-document-per-key Firestore model.

Schema per entity: PartitionKey="session", RowKey=<sanitized key>,
session_id=<str>, team_id=<str, "" when None>, channel_id=<str, "" when None>
"""
import logging

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_TABLE_NAME = "GtiSessions"
_PARTITION_KEY = "session"


def _sanitize_row_key(key: str) -> str:
    """Replace characters Table Storage forbids in a RowKey ('/', '\\', '#', '?')."""
    for ch in ("/", "\\", "#", "?"):
        key = key.replace(ch, "_")
    return key


def _table_client(cfg: Settings):
    if not cfg.azure_web_jobs_storage:
        return None
    client = TableClient.from_connection_string(cfg.azure_web_jobs_storage, table_name=_TABLE_NAME)
    try:
        client.create_table()
    except ResourceExistsError:
        pass
    return client


def _get_entity(key: str) -> dict | None:
    client = _table_client(settings)
    if client is None:
        return None
    try:
        return client.get_entity(partition_key=_PARTITION_KEY, row_key=_sanitize_row_key(key))
    except ResourceNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[SESSION] Failed to read session for key=%s (%s).", key, exc)
        return None


def get_session_id(key: str) -> str | None:
    """Return the stored GTI session_id for this key (Post ID or Conversation ID), or None."""
    if not key:
        return None
    entity = _get_entity(key)
    session_id = entity.get("session_id") if entity else None
    if session_id:
        logger.info("[SESSION] Found existing session_id=%s for key=%s", session_id, key)
    return session_id or None


def get_team_id(key: str) -> str | None:
    """Return the stored Team ID for this key, or None."""
    if not key:
        return None
    entity = _get_entity(key)
    team_id = entity.get("team_id") if entity else None
    return team_id or None


def get_channel_id(key: str) -> str | None:
    """Return the stored Channel ID for this key, or None."""
    if not key:
        return None
    entity = _get_entity(key)
    channel_id = entity.get("channel_id") if entity else None
    return channel_id or None


def set_session_id(
    key: str, session_id: str, team_id: str | None = None, channel_id: str | None = None
) -> None:
    """Persist the GTI session_id (and Team/Channel ID, for channel threads) for this key."""
    if not key or not session_id:
        return
    client = _table_client(settings)
    if client is None:
        return
    try:
        client.upsert_entity({
            "PartitionKey": _PARTITION_KEY,
            "RowKey": _sanitize_row_key(key),
            "session_id": session_id,
            "team_id": team_id or "",
            "channel_id": channel_id or "",
        })
        logger.info(
            "[SESSION] Stored session_id=%s team_id=%s channel_id=%s for key=%s",
            session_id, team_id or "-", channel_id or "-", key,
        )
    except Exception as exc:
        logger.error("[SESSION] Failed to write session for key=%s (%s).", key, exc)
