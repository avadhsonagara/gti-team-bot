"""
Converts a raw Bot Framework Activity JSON payload (the POST body Teams
sends to /api/messages) into the same attribute shape
app/teams/handlers.py, app/teams/attachments.py, and app/teams/thread.py
already expect — previously provided by the microsoft-teams-apps SDK's typed
Activity model. This lets that business logic stay unchanged (no SDK, no
async) beyond how it's constructed.

Field mapping mirrors the SDK's own aliasing (see
microsoft_teams.api.models.custom_base_model.CustomBaseModel): JSON is
camelCase, these attributes are the SDK's snake_case names, and Teams-specific
`team`/`channel` info is nested under the raw `channelData.team` /
`channelData.channel`, not top-level fields.
"""
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _account_ns(raw: Optional[dict]) -> Optional[SimpleNamespace]:
    if raw is None:
        return None
    return SimpleNamespace(
        id=raw.get("id"),
        name=raw.get("name"),
        aad_object_id=raw.get("aadObjectId") or raw.get("objectId"),
    )


def _conversation_ns(raw: Optional[dict]) -> SimpleNamespace:
    raw = raw or {}
    return SimpleNamespace(
        id=raw.get("id", ""),
        conversation_type=raw.get("conversationType", ""),
        tenant_id=raw.get("tenantId", ""),
    )


def _team_ns(raw: Optional[dict]) -> Optional[SimpleNamespace]:
    if raw is None:
        return None
    return SimpleNamespace(id=raw.get("id"), aad_group_id=raw.get("aadGroupId"))


def _channel_ns(raw: Optional[dict]) -> Optional[SimpleNamespace]:
    if raw is None:
        return None
    return SimpleNamespace(id=raw.get("id"))


def _attachment_ns(raw: dict) -> SimpleNamespace:
    return SimpleNamespace(
        content_type=raw.get("contentType"),
        content_url=raw.get("contentUrl"),
        content=raw.get("content"),
        name=raw.get("name"),
    )


def parse_activity(body: dict) -> SimpleNamespace:
    """Build the activity object app/teams/handlers.py, attachments.py, and thread.py expect."""
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
