"""
Shared local JSON-file storage — replaces Google Cloud Firestore for local
development, so no GCP project or credentials are needed to run this bot.
Used by app/output_format_store.py and app/gti/session_store.py to persist
the custom output-format instructions and per-thread GTI session mappings.

File shape:
{
  "output_format": "<custom formatting instructions, or empty string>",
  "sessions": {
    "<session_key>": {"session_id": "...", "team_id": "..." | null}
  }
}

Guarded by a single asyncio.Lock (read-modify-write for updates) since Teams
messages are handled concurrently on the shared event loop — file I/O itself
runs via asyncio.to_thread() to avoid blocking that loop.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("gti-teams-bot")

_LOCK = asyncio.Lock()


def _default_store() -> dict[str, Any]:
    return {"output_format": "", "sessions": {}}


def _read_sync(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_store()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[STORE] Failed to read local store at %s (%s) — starting fresh.", path, exc)
        return _default_store()
    data.setdefault("output_format", "")
    data.setdefault("sessions", {})
    return data


def _write_sync(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(path)  # atomic on POSIX — avoids a torn/partial file on crash


async def read_store(path: str) -> dict[str, Any]:
    """Read the whole store file (all fields, e.g. for a snapshot). Best-effort."""
    async with _LOCK:
        return await asyncio.to_thread(_read_sync, Path(path))


async def update_store(path: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    """Read-modify-write the store under a single lock, applying `mutate(data)` in place."""
    async with _LOCK:
        store_path = Path(path)
        data = await asyncio.to_thread(_read_sync, store_path)
        mutate(data)
        await asyncio.to_thread(_write_sync, store_path, data)
