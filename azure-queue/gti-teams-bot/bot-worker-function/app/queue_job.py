"""
Queue job deserialization and parsing for worker execution.

Validates and extracts activity payloads, placeholder activity IDs, and timestamps
from messages dequeued from Azure Storage Queue.
"""
from datetime import datetime, timezone
from typing import Optional


class InvalidJobPayload(Exception):
    """Raised when a dequeued message payload does not match the expected job structure."""


def get_job_kind(raw: dict) -> str:
    """
    Extract the job kind from a raw job dictionary.

    Args:
        raw: Decoded JSON dictionary from the storage queue.

    Returns:
        Job kind string ('message' or 'installationUpdateRemove').
    """
    return raw.get("kind") or "message"


def parse_job_payload(raw: dict) -> tuple[dict, Optional[str], datetime]:
    """
    Parse a message job payload into its components.

    Args:
        raw: Decoded JSON dictionary from the storage queue.

    Returns:
        Tuple of (activity_body, loading_activity_id, enqueued_at).

    Raises:
        InvalidJobPayload: If the payload does not contain a valid activity dictionary.
    """
    activity_body = raw.get("activity")
    if not isinstance(activity_body, dict):
        raise InvalidJobPayload("Job payload is missing a valid 'activity' object.")
    loading_activity_id = raw.get("loadingActivityId")
    enqueued_at = _parse_iso(raw.get("enqueuedAt")) or datetime.now(timezone.utc)
    return activity_body, loading_activity_id, enqueued_at


def _parse_iso(value) -> Optional[datetime]:
    """
    Parse an ISO 8601 formatted timestamp string into a timezone-aware datetime.

    Args:
        value: Timestamp string or object to parse.

    Returns:
        datetime object if parsing succeeds, or None if invalid or empty.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

