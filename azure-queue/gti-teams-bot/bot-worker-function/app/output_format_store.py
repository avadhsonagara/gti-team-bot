"""
Output-format instructions persistence (Azure Blob Storage).

main.bicep's outputFormatInstructions parameter seeds an initial value via
the OUTPUT_FORMAT_INSTRUCTIONS app setting. This module then makes a JSON
blob in the Function App's own storage account (AzureWebJobsStorage) the
durable source of truth — mirroring azure/rs-alerts/app/state_store.py's
cursor blob — so the format is persisted the same way across the two apps.
"""
import json
import logging
import threading

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from app.config import Settings

logger = logging.getLogger("gti-teams-bot")

_CONTAINER_NAME = "bot-config"
_BLOB_NAME = "output-format.json"

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
    blob_client = _blob_client(settings)
    if blob_client is None:
        return
    blob_client.upload_blob(json.dumps({"output_format": format_text}), overwrite=True)


def get_output_format(settings: Settings) -> str:
    """
    Return the current output-format instructions.

    Reads the JSON config blob if present; otherwise seeds it from the
    deploy-time OUTPUT_FORMAT_INSTRUCTIONS default so later reads (and any
    future config tooling) have a durable JSON source of truth instead of
    relying on the app setting forever.
    """
    blob_client = _blob_client(settings)
    if blob_client is None:
        return settings.output_format_instructions

    try:
        raw = blob_client.download_blob().readall()
        return json.loads(raw).get("output_format", "") or settings.output_format_instructions
    except ResourceNotFoundError:
        default = settings.output_format_instructions
        if default:
            _write_output_format(settings, default)
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("[CONFIG] output-format.json blob is unreadable — using deploy-time default.")
        return settings.output_format_instructions
