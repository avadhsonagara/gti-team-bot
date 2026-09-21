"""
Incremental cursor state storage backed by Azure Blob Storage.

Stores and retrieves the high-watermark timestamp cursor (`last_update_time`)
in an Azure Storage container to enable continuous incremental alert ingestion.
"""
import json
import logging
import time

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from app.config import Settings

logger = logging.getLogger("rs-alerts")

_CHECKPOINT_WRITE_RETRIES = 3
_CHECKPOINT_WRITE_BACKOFF_SECONDS = 1.0


def _blob_client(settings: Settings):
    """
    Construct and return a BlobClient for the configured state blob.

    Ensures the destination container exists prior to returning the client.

    Args:
        settings: Application settings containing storage credentials and container names.

    Returns:
        azure.storage.blob.BlobClient configured for the state blob.

    Raises:
        RuntimeError: If AzureWebJobsStorage is not configured.
    """
    if not settings.azure_web_jobs_storage:
        raise RuntimeError("AzureWebJobsStorage is not configured — cannot persist the alert cursor.")

    service_client = BlobServiceClient.from_connection_string(settings.azure_web_jobs_storage)
    container_client = service_client.get_container_client(settings.state_container_name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
    return container_client.get_blob_client(settings.state_blob_name)


def read_cursor(settings: Settings) -> str | None:
    """
    Read the incremental cursor timestamp from Azure Blob Storage.

    Args:
        settings: Application settings containing storage configurations.

    Returns:
        Last recorded RFC 3339 update timestamp string, or None if no valid cursor exists.
    """
    blob_client = _blob_client(settings)
    try:
        raw = blob_client.download_blob().readall()
    except ResourceNotFoundError:
        logger.info("[RS-ALERTS CHECKPOINT] No previous cursor blob found; initializing new cursor.")
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("[RS-ALERTS CHECKPOINT] State blob is corrupted or unreadable; starting fresh.")
        return None

    cursor = data.get("last_update_time")
    if cursor:
        logger.info("[RS-ALERTS CHECKPOINT] Loaded existing cursor timestamp: %s", cursor)
    else:
        logger.info("[RS-ALERTS CHECKPOINT] Cursor blob contained no timestamp; starting fresh.")
    return cursor


def write_cursor(settings: Settings, update_time: str) -> None:
    """
    Persist the incremental cursor timestamp to Azure Blob Storage with retry logic.

    Args:
        settings: Application settings containing storage configurations.
        update_time: RFC 3339 formatted timestamp string to persist.

    Raises:
        Exception: If writing to blob storage fails after exhausting all retry attempts.
    """
    blob_client = _blob_client(settings)
    payload = json.dumps({"last_update_time": update_time})

    for attempt in range(_CHECKPOINT_WRITE_RETRIES):
        try:
            blob_client.upload_blob(payload, overwrite=True)
            logger.info("[RS-ALERTS CHECKPOINT] Saved cursor timestamp %s to blob storage.", update_time)
            return
        except Exception:
            if attempt == _CHECKPOINT_WRITE_RETRIES - 1:
                raise
            delay = _CHECKPOINT_WRITE_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "[RS-ALERTS RETRY] Checkpoint write failed (attempt %d/%d) — retrying in %.1fs.",
                attempt + 1, _CHECKPOINT_WRITE_RETRIES, delay,
            )
            time.sleep(delay)
