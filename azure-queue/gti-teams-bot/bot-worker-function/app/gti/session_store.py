"""
Persists the mapping from a Teams channel thread to its GTI Agentic
session_id, so a second message in the same thread continues the same GTI
session instead of starting a fresh one each time.

Only channel messages ever get a row here. Personal (1:1) and group chats
never persist a session — every message there always starts a fresh GTI
session, so job_processor.py never calls into this module for those scopes.

Azure Table Storage-backed, one table ("GtiSessions"), one row per channel
thread:
  PartitionKey = "<team_id>:<channel_id>"   # scopes every row to one channel
  RowKey       = "<team_post_id>"           # the thread's root Post ID
  gti_session_id = "<str>"

`upsert_entity` is atomic per partition/row key, so two threads writing
different session keys concurrently can never clobber each other's entry —
unlike a single shared JSON blob, where a full read-modify-write-whole-file
cycle let a second thread's write silently erase a first thread's just-added
key.

When the bot is removed from a team, delete_team_sessions() deletes every
row whose PartitionKey starts with "<team_id>:" — i.e. every channel of that
team — via a PartitionKey range query, since Bot Framework's removal event
fires once per team (not once per channel), so a specific channel_id usually
isn't available at cleanup time.
"""
import logging
import threading

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient

from app.config import Settings, settings

logger = logging.getLogger("gti-teams-bot")

_TABLE_NAME = "GtiSessions"

# Both the TableClient instance and its create_table() call only need to
# happen once per worker instance lifetime — without this cache, every
# single get/set call constructed a fresh client and (until the flag was
# added) re-issued create_table() as an extra HTTP round-trip.
_table_client_instance: TableClient | None = None
_table_client_lock = threading.Lock()

# Per-thread locks so two concurrent requests for the SAME channel thread
# serialize around read-session -> maybe-create-in-GTI -> write-session,
# instead of both reading "no session yet", both creating a GTI session, and
# one write silently orphaning the other. Process-wide only (scoped to this
# worker instance's memory, not distributed across instances).
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _sanitize(value: str) -> str:
    """Replace characters Table Storage forbids in a PartitionKey/RowKey ('/', '\\', '#', '?')."""
    for ch in ("/", "\\", "#", "?"):
        value = value.replace(ch, "_")
    return value


def _escape_odata_string(value: str) -> str:
    """Escape a value for safe interpolation into an OData filter string literal."""
    return value.replace("'", "''")


def _partition_key(team_id: str, channel_id: str) -> str:
    return f"{_sanitize(team_id)}:{_sanitize(channel_id)}"


def session_lock(team_id: str, channel_id: str, team_post_id: str) -> threading.Lock:
    """
    Return a process-wide lock scoped to this channel thread. Hold it around
    the read-session -> maybe-create-in-GTI -> write-session sequence so two
    concurrent requests for the same thread can't both create a GTI session
    and silently orphan one.
    """
    lock_key = f"{_partition_key(team_id, channel_id)}::{_sanitize(team_post_id)}"
    with _session_locks_guard:
        lock = _session_locks.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _session_locks[lock_key] = lock
        return lock


def _table_client(cfg: Settings):
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
    """Return the stored GTI session_id for this channel thread, or None."""
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
    """Persist the GTI session_id for this channel thread."""
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
    Delete every stored session for every channel of a team — called when
    the bot is removed from that team, so Table Storage doesn't accumulate
    rows for a team that no longer has the bot installed.

    A PartitionKey range query ("team_id:" <= PartitionKey < "team_id;" —
    ':' sorts immediately before ';' in ASCII, so this matches exactly the
    partition keys that start with "team_id:", whatever channel_id follows)
    rather than an exact-partition delete, since Bot Framework's removal
    event fires once per team, not once per channel — a specific channel_id
    usually isn't available here.
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
