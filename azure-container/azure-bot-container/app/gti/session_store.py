"""
Persists the mapping from a Teams channel thread to its GTI Agentic
session_id, so a second message in the same thread continues the same GTI
session instead of starting a fresh one each time.

Azure Table Storage-backed — one entity (row) per thread, in this app's own
storage account (STORAGE_CONNECTION_STRING). `upsert_entity` is atomic per
partition/row key, so two requests writing different threads concurrently
can never clobber each other's entry.

Schema per entity:
  PartitionKey = "<team_id>:<channel_id>" (sanitized)
  RowKey       = <thread Post ID> (sanitized)
  session_id, team_id, channel_id = <str>

Partitioning by team+channel (rather than a single constant partition, as an
earlier version of this module did) matters for two reasons:
  1. Teams message/Post IDs are NOT globally unique — Microsoft's own Graph
     API examples show a message's `id` is just its creation time in epoch
     milliseconds, unique only within the channel/chat that created it. Two
     different channels' threads could in principle produce the same Post
     ID; keying purely by Post ID risked one thread's session silently
     overwriting or leaking into an unrelated one.
  2. A single shared partition put every thread this bot has ever seen,
     across every team, on one Table Storage partition, capped at that
     partition's own throughput ceiling — partitioning by team+channel
     spreads that load out instead.

Fully async — azure.data.tables.aio. A single TableClient is kept alive for
the process lifetime (see _get_client()), and create_table() is only ever
attempted once — after the table exists, every later call would just get
back a 409 (ResourceExistsError) for no reason.
"""
import asyncio
import logging

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables.aio import TableClient

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_TABLE_NAME = "GtiSessions"

# Separate partition, same table: caches the last-known team_id for a
# channel, independent of any specific thread. channelData.team isn't always
# present on a given activity (see _partition_key()'s docstring), but
# Microsoft Graph's channel-message APIs require team_id in the URL with no
# way to derive it from channel_id alone — so when the current activity is
# missing it, get_team_id_for_channel() recovers one we saw and persisted on
# an earlier message in the same channel instead. Refreshed opportunistically
# by set_session_id() every time both values are known, which is most calls.
_CHANNEL_TEAM_PARTITION = "channel_team_map"

_client: TableClient | None = None
_table_ensured = False
_lock = asyncio.Lock()


def _sanitize(value: str) -> str:
    """Replace characters Table Storage forbids in a PartitionKey/RowKey ('/', '\\', '#', '?')."""
    for ch in ("/", "\\", "#", "?"):
        value = value.replace(ch, "_")
    return value


def _partition_key(team_id: str, channel_id: str) -> str:
    """
    Scope rows to their own channel. channel_id is used alone when available:
    it's structurally embedded in conversation.id (see get_channel_id()'s
    fallback in app/teams/thread.py), so it's reliably present and, more
    importantly, *stable* across every message in a channel. team_id comes
    from channelData.team, which Teams does not always populate on every
    activity — a documented inconsistency, worse on some mobile clients.
    Mixing an unreliable team_id into every row's key would reintroduce the
    exact failure mode this partitioning is meant to avoid: the same thread
    computing a different key message-to-message and losing session
    continuity. team_id is only used as a fallback when channel_id is
    itself unavailable.
    """
    return _sanitize(channel_id or team_id or "-")


async def _get_client() -> TableClient | None:
    """Lazily create the shared TableClient and ensure the table exists — both exactly once."""
    global _client, _table_ensured
    if not settings.storage_connection_string:
        return None

    if _client is None:
        async with _lock:
            if _client is None:
                _client = TableClient.from_connection_string(settings.storage_connection_string, table_name=_TABLE_NAME)

    if not _table_ensured:
        async with _lock:
            if not _table_ensured:
                try:
                    await _client.create_table()
                except ResourceExistsError:
                    pass
                _table_ensured = True

    return _client


async def close() -> None:
    """Close the shared TableClient (call on app shutdown)."""
    global _client, _table_ensured
    if _client is not None:
        await _client.close()
        _client = None
    _table_ensured = False


async def _get_entity(team_id: str, channel_id: str, key: str) -> dict | None:
    client = await _get_client()
    if client is None:
        return None
    try:
        return await client.get_entity(partition_key=_partition_key(team_id, channel_id), row_key=_sanitize(key))
    except ResourceNotFoundError:
        return None
    except Exception as exc:
        logger.warning(
            "[SESSION] Failed to read session for team=%s channel=%s key=%s (%s).", team_id, channel_id, key, exc,
        )
        return None


async def get_session_id(team_id: str, channel_id: str, key: str) -> str | None:
    """Return the stored GTI session_id for this team/channel's thread key, or None."""
    if not key:
        return None
    entity = await _get_entity(team_id, channel_id, key)
    session_id = entity.get("session_id") if entity else None
    if session_id:
        logger.info(
            "[SESSION] Found existing session_id=%s for team=%s channel=%s key=%s",
            session_id, team_id, channel_id, key,
        )
    return session_id or None


async def set_session_id(team_id: str, channel_id: str, key: str, session_id: str) -> None:
    """Persist the GTI session_id for this team/channel's thread key."""
    if not key or not session_id:
        return
    client = await _get_client()
    if client is None:
        return
    try:
        await client.upsert_entity({
            "PartitionKey": _partition_key(team_id, channel_id),
            "RowKey": _sanitize(key),
            "session_id": session_id,
            "team_id": team_id or "",
            "channel_id": channel_id or "",
        })
        logger.info(
            "[SESSION] Stored session_id=%s for team=%s channel=%s key=%s",
            session_id, team_id or "-", channel_id or "-", key,
        )
    except Exception as exc:
        logger.error(
            "[SESSION] Failed to write session for team=%s channel=%s key=%s (%s).", team_id, channel_id, key, exc,
        )

    if team_id and channel_id:
        await _remember_channel_team(channel_id, team_id)


async def _remember_channel_team(channel_id: str, team_id: str) -> None:
    """Record channel_id -> team_id so a later activity missing channelData.team can still be resolved."""
    client = await _get_client()
    if client is None:
        return
    try:
        await client.upsert_entity({
            "PartitionKey": _CHANNEL_TEAM_PARTITION,
            "RowKey": _sanitize(channel_id),
            "team_id": team_id,
        })
    except Exception as exc:
        logger.warning("[SESSION] Failed to cache team_id for channel=%s (%s).", channel_id, exc)


async def get_team_id_for_channel(channel_id: str) -> str | None:
    """
    Best-effort lookup of the last known team_id for a channel, keyed
    independently of any specific thread. Used as a fallback when the
    current activity's own channelData.team is missing.
    """
    if not channel_id:
        return None
    client = await _get_client()
    if client is None:
        return None
    try:
        entity = await client.get_entity(partition_key=_CHANNEL_TEAM_PARTITION, row_key=_sanitize(channel_id))
        return entity.get("team_id") or None
    except ResourceNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[SESSION] Failed to read cached team_id for channel=%s (%s).", channel_id, exc)
        return None
