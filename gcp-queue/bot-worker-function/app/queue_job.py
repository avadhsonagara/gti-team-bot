"""
Job deserialization for worker execution: unwrapping the Pub/Sub push
envelope, then parsing the job payload it carries.
"""
import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Optional


class InvalidJobPayload(Exception):
    """Raised when a pushed message's envelope or payload doesn't match the expected shape."""


def unwrap_pubsub_envelope(envelope: dict) -> tuple[dict, str]:
    """
    Decode a Pub/Sub push request body into (job_payload, message_id).

    A push request body looks like:
      {"message": {"data": "<base64 JSON>", "messageId": "...", "publishTime": "..."},
       "subscription": "projects/.../subscriptions/..."}

    Args:
        envelope: Parsed JSON body of the inbound push HTTP request.

    Returns:
        (decoded job payload dict, Pub/Sub messageId string).

    Raises:
        InvalidJobPayload: If the envelope doesn't match Pub/Sub's push shape,
            or its data isn't valid base64-encoded JSON.
    """
    if not isinstance(envelope, dict):
        raise InvalidJobPayload("Push request body is not a JSON object.")

    message = envelope.get("message")
    if not isinstance(message, dict):
        raise InvalidJobPayload("Push envelope is missing a valid 'message' object.")

    message_id = message.get("messageId") or ""
    raw_data = message.get("data")
    if not isinstance(raw_data, str) or not raw_data:
        raise InvalidJobPayload("Push envelope's message is missing 'data'.")

    try:
        decoded_bytes = base64.b64decode(raw_data)
    except (binascii.Error, ValueError) as exc:
        raise InvalidJobPayload(f"Push envelope's message.data is not valid base64: {exc}") from exc

    try:
        payload = json.loads(decoded_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidJobPayload(f"Push envelope's decoded message.data is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise InvalidJobPayload("Decoded job payload is not a JSON object.")

    return payload, message_id


def get_job_kind(raw: dict) -> str:
    """Extract the job kind from a decoded job payload dictionary."""
    return raw.get("kind") or "message"


def parse_job_payload(raw: dict) -> tuple[dict, Optional[str], datetime]:
    """
    Parse a message job payload into its components.

    Args:
        raw: Decoded job payload dictionary (already unwrapped from Pub/Sub).

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
    """Parse an ISO 8601 formatted timestamp string into a timezone-aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
