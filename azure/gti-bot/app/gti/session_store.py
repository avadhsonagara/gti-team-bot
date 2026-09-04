"""
Persists the mapping from a Teams thread (Post ID) or conversation to its
GTI Agentic session_id (plus the Team ID it belongs to, for channel threads),
so a second message in the same thread continues the same GTI session
instead of starting a fresh one each time.

Azure Blob Storage-backed — one JSON blob (gti-sessions.json) holding the
whole key -> {session_id, team_id} map, in the Function App's own storage
account (AzureWebJobsStorage), mirroring app/output_format_store.py's exact
pattern (same container: "bot-config").

Schema: { "<session_key>": {"session_id": "<gti_session_id>", "team_id": "<team_id_or_null>"}, ... }
"""
import json
import logging

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_CONTAINER_NAME = "bot-config"
_BLOB_NAME = "gti-sessions.json"


def _blob_client(cfg: Settings):
    if not cfg.azure_web_jobs_storage:
        return None
    service_client = BlobServiceClient.from_connection_string(cfg.azure_web_jobs_storage)
    container_client = service_client.get_container_client(_CONTAINER_NAME)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
    return container_client.get_blob_client(_BLOB_NAME)


def _load() -> dict:
    """Read the whole session map from blob storage. Empty dict if absent or unreadable."""
    blob_client = _blob_client(settings)
    if blob_client is None:
        return {}
    try:
        raw = blob_client.download_blob().readall()
        return json.loads(raw)
    except ResourceNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("[SESSION] gti-sessions.json blob is unreadable — starting empty.")
        return {}


def _save(data: dict) -> None:
    blob_client = _blob_client(settings)
    if blob_client is None:
        return
    try:
        blob_client.upload_blob(json.dumps(data), overwrite=True)
    except Exception as exc:
        logger.error("[SESSION] Failed to write gti-sessions.json blob: %s", exc)


def get_session_id(key: str) -> str | None:
    """Return the stored GTI session_id for this key (Post ID or Conversation ID), or None."""
    if not key:
        return None
    entry = _load().get(key)
    session_id = entry.get("session_id") if isinstance(entry, dict) else entry
    if session_id:
        logger.info("[SESSION] Found existing session_id=%s for key=%s", session_id, key)
    return session_id


def get_team_id(key: str) -> str | None:
    """Return the stored Team ID for this key, or None."""
    if not key:
        return None
    entry = _load().get(key)
    return entry.get("team_id") if isinstance(entry, dict) else None


def set_session_id(key: str, session_id: str, team_id: str | None = None) -> None:
    """Persist the GTI session_id (and Team ID, for channel threads) for this key."""
    if not key or not session_id:
        return
    data = _load()
    data[key] = {"session_id": session_id, "team_id": team_id or None}
    _save(data)
    logger.info("[SESSION] Stored session_id=%s team_id=%s for key=%s", session_id, team_id or "-", key)
