"""
Azure Table Storage persistence for GTI channel thread sessions.

Maintains mappings between Teams channel thread post IDs and active GTI session IDs,
allowing subsequent replies within the same thread to continue previous conversational context.
"""
import logging
import threading

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_TABLE_NAME = "GtiSessions"

_table_client_instance: TableClient | None = None
_table_client_lock = threading.Lock()


def _sanitize(value: str) -> str:
    """
    Sanitize a string for use in Azure Table Storage PartitionKey and RowKey fields.

    Args:
        value: Raw key string.

    Returns:
        Sanitized string with forbidden characters replaced by underscores.
    """
    for ch in ("/", "\\", "#", "?"):
        value = value.replace(ch, "_")
    return value


def _escape_odata_string(value: str) -> str:
    """
    Escape single quotes for inclusion in an OData query filter string literal.

    Args:
        value: Input string to escape.

    Returns:
        OData-safe escaped string.
    """
    return value.replace("'", "''")


def _partition_key(team_id: str, channel_id: str) -> str:
    """
    Construct a partition key from team and channel identifiers.

    Args:
        team_id: Teams team identifier.
        channel_id: Teams channel identifier.

    Returns:
        Combined sanitized partition key string.
    """
    return f"{_sanitize(team_id)}:{_sanitize(channel_id)}"


def _table_client(cfg: Settings) -> TableClient | None:
    """
    Retrieve or lazily initialize the shared TableClient instance for session storage.

    Args:
        cfg: Application Settings instance.

    Returns:
        Configured TableClient instance, or None if connection string is missing.
    """
    global _table_client_instance
    if not cfg.azure_web_jobs_storage:
        return None
    if _table_client_instance is None:
        with _table_client_lock:
            if _table_client_instance is None:
                client = TableClient.from_connection_string(cfg.azure_web_jobs_storage, table_name=_TABLE_NAME)
                try:
                    client.create_table()
                except ResourceExistsError:
                    pass
                _table_client_instance = client
    return _table_client_instance


def get_session_id(team_id: str, channel_id: str, team_post_id: str) -> str | None:
    """
    Retrieve the stored GTI session ID for a channel thread.

    Args:
        team_id: Teams team identifier.
        channel_id: Teams channel identifier.
        team_post_id: Root post ID of the thread.

    Returns:
        Active GTI session ID string if found, or None.
    """
    if not (team_id and channel_id and team_post_id):
        return None
    client = _table_client(settings)
    if client is None:
        return None
    try:
        entity = client.get_entity(
            partition_key=_partition_key(team_id, channel_id),
            row_key=_sanitize(team_post_id),
        )
        session_id = entity.get("gti_session_id")
        if session_id:
            logger.info(
                "[SESSION] Found existing session_id=%s for team=%s channel=%s post=%s",
                session_id, team_id, channel_id, team_post_id,
            )
        return session_id or None
    except ResourceNotFoundError:
        return None
    except Exception as exc:
        logger.warning(
            "[SESSION] Failed to read session for team=%s channel=%s post=%s (%s).",
            team_id, channel_id, team_post_id, exc,
        )
        return None


def set_session_id(team_id: str, channel_id: str, team_post_id: str, gti_session_id: str) -> None:
    """
    Persist or update the GTI session ID associated with a channel thread.

    Args:
        team_id: Teams team identifier.
        channel_id: Teams channel identifier.
        team_post_id: Root post ID of the thread.
        gti_session_id: GTI session ID to store.
    """
    if not (team_id and channel_id and team_post_id and gti_session_id):
        return
    client = _table_client(settings)
    if client is None:
        return
    try:
        client.upsert_entity({
            "PartitionKey": _partition_key(team_id, channel_id),
            "RowKey": _sanitize(team_post_id),
            "gti_session_id": gti_session_id,
        })
        logger.info(
            "[SESSION] Stored session_id=%s for team=%s channel=%s post=%s",
            gti_session_id, team_id, channel_id, team_post_id,
        )
    except Exception as exc:
        logger.error(
            "[SESSION] Failed to write session for team=%s channel=%s post=%s (%s).",
            team_id, channel_id, team_post_id, exc,
        )


def delete_team_sessions(team_id: str) -> None:
    """
    Delete all stored sessions for every channel belonging to a specified team.

    Args:
        team_id: Teams team identifier whose session records should be removed.
    """
    if not team_id:
        return
    client = _table_client(settings)
    if client is None:
        return
    prefix = _escape_odata_string(_sanitize(team_id)) + ":"
    upper_bound = _escape_odata_string(_sanitize(team_id)) + ";"
    try:
        entities = client.query_entities(
            query_filter=f"PartitionKey ge '{prefix}' and PartitionKey lt '{upper_bound}'"
        )
        deleted = 0
        for entity in entities:
            client.delete_entity(partition_key=entity["PartitionKey"], row_key=entity["RowKey"])
            deleted += 1
        logger.info("[SESSION] Deleted %d session(s) across all channels for team=%s", deleted, team_id)
    except Exception as exc:
        logger.error("[SESSION] Failed to delete sessions for team=%s (%s).", team_id, exc)
