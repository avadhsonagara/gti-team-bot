"""
Parser for Microsoft Bot Framework Activity payloads.

Converts raw inbound JSON activities into structured SimpleNamespace objects
providing typed access to activity attributes, senders, conversations, teams,
channels, and attachments.
"""
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

_FRACTIONAL_SECONDS_RE = re.compile(r"\.\d+")


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 timestamp string into a datetime object.

    Args:
        value: ISO formatted timestamp string or None.

    Returns:
        Parsed datetime object or None if parsing fails.
    """
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    match = _FRACTIONAL_SECONDS_RE.search(value)
    if match:
        fractional = match.group()[1:][:6].ljust(6, "0")
        value = f"{value[:match.start()]}.{fractional}{value[match.end():]}"
    try:
        return datetime.fromisoformat(value)
    except (ValueError, AttributeError):
        return None


def _account_ns(raw: Optional[dict]) -> Optional[SimpleNamespace]:
    """
    Convert an account dictionary into a structured namespace.

    Args:
        raw: Dictionary containing account data (id, name, aadObjectId).

    Returns:
        SimpleNamespace with id, name, and aad_object_id attributes, or None.
    """
    if raw is None:
        return None
    return SimpleNamespace(
        id=raw.get("id"),
        name=raw.get("name"),
        aad_object_id=raw.get("aadObjectId") or raw.get("objectId"),
    )


def _conversation_ns(raw: Optional[dict]) -> SimpleNamespace:
    """
    Convert conversation metadata into a structured namespace.

    Args:
        raw: Dictionary containing conversation data.

    Returns:
        SimpleNamespace with id, conversation_type, and tenant_id attributes.
    """
    raw = raw or {}
    return SimpleNamespace(
        id=raw.get("id", ""),
        conversation_type=raw.get("conversationType", ""),
        tenant_id=raw.get("tenantId", ""),
    )


def _team_ns(raw: Optional[dict]) -> Optional[SimpleNamespace]:
    """
    Convert team metadata into a structured namespace.

    Args:
        raw: Dictionary containing team data.

    Returns:
        SimpleNamespace with id and aad_group_id attributes, or None.
    """
    if raw is None:
        return None
    return SimpleNamespace(id=raw.get("id"), aad_group_id=raw.get("aadGroupId"))


def _channel_ns(raw: Optional[dict]) -> Optional[SimpleNamespace]:
    """
    Convert channel metadata into a structured namespace.

    Args:
        raw: Dictionary containing channel data.

    Returns:
        SimpleNamespace with id attribute, or None.
    """
    if raw is None:
        return None
    return SimpleNamespace(id=raw.get("id"))


def _attachment_ns(raw: dict) -> SimpleNamespace:
    """
    Convert an attachment dictionary into a structured namespace.

    Args:
        raw: Dictionary containing attachment attributes.

    Returns:
        SimpleNamespace with content_type, content_url, content, and name.
    """
    return SimpleNamespace(
        content_type=raw.get("contentType"),
        content_url=raw.get("contentUrl"),
        content=raw.get("content"),
        name=raw.get("name"),
    )


def parse_activity(body: dict) -> SimpleNamespace:
    """
    Parse a raw Bot Framework Activity dictionary into a structured namespace.

    Args:
        body: Raw Activity JSON dictionary received from Teams.

    Returns:
        SimpleNamespace representing the parsed activity.
    """
    channel_data: dict[str, Any] = body.get("channelData") or {}

    return SimpleNamespace(
        type=body.get("type", ""),
        id=body.get("id", ""),
        text=body.get("text", "") or "",
        timestamp=_parse_timestamp(body.get("timestamp")),
        service_url=body.get("serviceUrl", ""),
        channel_data=channel_data,
        conversation=_conversation_ns(body.get("conversation")),
        from_=_account_ns(body.get("from")),
        team=_team_ns(channel_data.get("team")),
        channel=_channel_ns(channel_data.get("channel")),
        attachments=[_attachment_ns(a) for a in (body.get("attachments") or [])],
    )
