"""
Output-format instructions persistence (Azure Blob Storage).

main.bicep's outputFormatInstructions parameter seeds an initial value via
the OUTPUT_FORMAT_INSTRUCTIONS app setting. This module then makes a JSON
blob in the Function App's own storage account (AzureWebJobsStorage) the
durable source of truth — mirroring azure/rs-alerts/app/state_store.py's
cursor blob — so the format is persisted the same way across the two apps.

The value changes rarely, so it's cached in memory for OUTPUT_FORMAT_CACHE_TTL_SECONDS
instead of being read from Blob Storage on every message.
"""
import json
import logging
import threading
import time

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from app.config import Settings
from app.constants import OUTPUT_FORMAT_CACHE_TTL_SECONDS

logger = logging.getLogger("gti-teams-bot")

_CONTAINER_NAME = "bot-config"
_BLOB_NAME = "output-format.json"

_cache_value: str | None = None
_cache_fetched_at: float | None = None

# Both the container client and its create_container() call only need to
# happen once per worker instance lifetime — without this cache, every
# single job (get_output_format() runs on every message) constructed a fresh
# BlobServiceClient/container client and re-issued create_container() as an
# extra HTTP round-trip.
_container_client_instance = None
_container_client_lock = threading.Lock()


def _blob_client(settings: Settings):
    global _container_client_instance
    if not settings.azure_web_jobs_storage:
        return None
    if _container_client_instance is None:
        with _container_client_lock:
            if _container_client_instance is None:
                service_client = BlobServiceClient.from_connection_string(settings.azure_web_jobs_storage)
                container_client = service_client.get_container_client(_CONTAINER_NAME)
                try:
                    container_client.create_container()
                except ResourceExistsError:
                    pass
                _container_client_instance = container_client
    return _container_client_instance.get_blob_client(_BLOB_NAME)


def _write_output_format(settings: Settings, format_text: str) -> None:
    global _cache_value, _cache_fetched_at
    blob_client = _blob_client(settings)
    if blob_client is None:
        return
    blob_client.upload_blob(json.dumps({"output_format": format_text}), overwrite=True)
    _cache_value = format_text
    _cache_fetched_at = time.monotonic()


def get_output_format(settings: Settings) -> str:
    """
    Return the current output-format instructions.

    Reads the JSON config blob if present; otherwise seeds it from the
    deploy-time OUTPUT_FORMAT_INSTRUCTIONS default so later reads (and any
    future config tooling) have a durable JSON source of truth instead of
    relying on the app setting forever.

    Cached in memory for OUTPUT_FORMAT_CACHE_TTL_SECONDS, since this rarely changes and
    doesn't need a Blob Storage read on every message.
    """
    global _cache_value, _cache_fetched_at

    now = time.monotonic()
    if _cache_fetched_at is not None and (now - _cache_fetched_at) < OUTPUT_FORMAT_CACHE_TTL_SECONDS:
        return _cache_value

    blob_client = _blob_client(settings)
    if blob_client is None:
        return settings.output_format_instructions

    try:
        raw = blob_client.download_blob().readall()
        result = json.loads(raw).get("output_format", "") or settings.output_format_instructions
    except ResourceNotFoundError:
        default = settings.output_format_instructions
        if default:
            _write_output_format(settings, default)
        result = default
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("[CONFIG] output-format.json blob is unreadable — using deploy-time default.")
        result = settings.output_format_instructions

    _cache_value = result
    _cache_fetched_at = now
    return result
