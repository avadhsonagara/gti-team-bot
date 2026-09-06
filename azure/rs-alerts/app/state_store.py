"""
Incremental cursor persistence (Azure Blob Storage).

Flex Consumption Function App instances are ephemeral and can scale to zero
between timer ticks, so — unlike the original gti-alerts/state.json — the
cursor can't live on local disk. It's stored instead as a small JSON blob in
the same storage account the Function App already uses (AzureWebJobsStorage),
in a dedicated container.
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
    """Read the incremental cursor (last seen audit.update_time) from blob storage."""
    blob_client = _blob_client(settings)
    try:
        raw = blob_client.download_blob().readall()
    except ResourceNotFoundError:
        return None

    try:
        return json.loads(raw).get("last_update_time")
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("State blob is unreadable — starting fresh.")
        return None


def write_cursor(settings: Settings, update_time: str) -> None:
    """Write the incremental cursor to blob storage.

    The checkpoint is written after the alert has already been delivered to
    Teams, so a transient failure here (rather than a genuine one) would
    otherwise cause that alert to be re-sent on the next run. Retry with
    backoff to close most of that window before giving up and propagating.
    """
    blob_client = _blob_client(settings)
    payload = json.dumps({"last_update_time": update_time})

    for attempt in range(_CHECKPOINT_WRITE_RETRIES):
        try:
            blob_client.upload_blob(payload, overwrite=True)
            return
        except Exception:
            if attempt == _CHECKPOINT_WRITE_RETRIES - 1:
                raise
            delay = _CHECKPOINT_WRITE_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "Checkpoint write failed (attempt %d/%d) — retrying in %.1fs.",
                attempt + 1, _CHECKPOINT_WRITE_RETRIES, delay,
            )
            time.sleep(delay)
