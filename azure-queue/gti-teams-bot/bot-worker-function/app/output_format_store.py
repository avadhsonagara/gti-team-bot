"""
Output format instructions persistence in Azure Blob Storage.

Reads and persists customizable response formatting instructions to a JSON blob
in Azure Blob Storage, maintaining an in-memory cache to minimize storage transactions.
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

_container_client_instance = None
_container_client_lock = threading.Lock()


def _blob_client(settings: Settings):
    """
    Retrieve or initialize the BlobClient for the output format configuration blob.

    Args:
        settings: Application Settings instance.

    Returns:
        Configured BlobClient instance, or None if storage connection string is missing.
    """
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
    """
    Persist output format text to Azure Blob Storage and update local memory cache.

    Args:
        settings: Application Settings instance.
        format_text: Formatting instructions string to persist.
    """
    global _cache_value, _cache_fetched_at
    blob_client = _blob_client(settings)
    if blob_client is None:
        return
    blob_client.upload_blob(json.dumps({"output_format": format_text}), overwrite=True)
    _cache_value = format_text
    _cache_fetched_at = time.monotonic()


def get_output_format(settings: Settings) -> str:
    """
    Retrieve current output formatting instructions.

    Returns cached instructions if fresh; otherwise attempts to download from
    Azure Blob Storage or seeds initial instructions from application settings.

    Args:
        settings: Application Settings instance.

    Returns:
        Formatting instructions string.
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
