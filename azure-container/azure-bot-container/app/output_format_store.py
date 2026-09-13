"""
Output-format instructions persistence (Azure Blob Storage).

main.bicep's outputFormatInstructions parameter seeds an initial value via
the OUTPUT_FORMAT_INSTRUCTIONS app setting. This module then makes a JSON
blob in this app's own storage account (STORAGE_CONNECTION_STRING) the
durable source of truth — mirroring azure/rs-alerts/app/state_store.py's
cursor blob — so the format is persisted the same way across the two apps.

Fully async — azure.storage.blob.aio. A single BlobServiceClient is kept
alive for the process lifetime (see _get_container_client()), and
create_container() is only ever attempted once — after the container
exists, every later call would just get back a 409 (ResourceExistsError)
for no reason.
"""
import asyncio
import json
import logging

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from app.config import Settings

logger = logging.getLogger("gti-teams-bot")

_CONTAINER_NAME = "bot-config"
_BLOB_NAME = "output-format.json"

_service_client: BlobServiceClient | None = None
_container_client: ContainerClient | None = None
_container_ensured = False
_lock = asyncio.Lock()


async def _get_container_client(settings: Settings) -> ContainerClient | None:
    """Lazily create the shared clients and ensure the container exists — both exactly once."""
    global _service_client, _container_client, _container_ensured
    if not settings.storage_connection_string:
        return None

    if _container_client is None:
        async with _lock:
            if _container_client is None:
                _service_client = BlobServiceClient.from_connection_string(settings.storage_connection_string)
                _container_client = _service_client.get_container_client(_CONTAINER_NAME)

    if not _container_ensured:
        async with _lock:
            if not _container_ensured:
                try:
                    await _container_client.create_container()
                except ResourceExistsError:
                    pass
                _container_ensured = True

    return _container_client


async def close() -> None:
    """Close the shared Blob clients (call on app shutdown)."""
    global _service_client, _container_client, _container_ensured
    if _service_client is not None:
        await _service_client.close()
        _service_client = None
    _container_client = None
    _container_ensured = False


async def _write_output_format(settings: Settings, format_text: str) -> None:
    container_client = await _get_container_client(settings)
    if container_client is None:
        return
    blob_client = container_client.get_blob_client(_BLOB_NAME)
    await blob_client.upload_blob(json.dumps({"output_format": format_text}), overwrite=True)


async def get_output_format(settings: Settings) -> str:
    """
    Return the current output-format instructions.

    Reads the JSON config blob if present; otherwise seeds it from the
    deploy-time OUTPUT_FORMAT_INSTRUCTIONS default so later reads (and any
    future config tooling) have a durable JSON source of truth instead of
    relying on the app setting forever.
    """
    container_client = await _get_container_client(settings)
    if container_client is None:
        return settings.output_format_instructions

    blob_client = container_client.get_blob_client(_BLOB_NAME)

    try:
        downloader = await blob_client.download_blob()
        raw = await downloader.readall()
        return json.loads(raw).get("output_format", "") or settings.output_format_instructions
    except ResourceNotFoundError:
        default = settings.output_format_instructions
        if default:
            await blob_client.upload_blob(json.dumps({"output_format": default}), overwrite=True)
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("[CONFIG] output-format.json blob is unreadable — using deploy-time default.")
        return settings.output_format_instructions
