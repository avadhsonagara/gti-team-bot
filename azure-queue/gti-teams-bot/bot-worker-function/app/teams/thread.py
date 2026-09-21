"""
Microsoft Teams channel thread context retrieval and formatting.

Fetches previous messages in a Teams channel thread via Microsoft Graph API,
extracts text from HTML bodies and Adaptive Cards, filters out placeholder messages,
and formats the conversation history into a transcript for the GTI prompt.
"""
import html as html_lib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings
from app.constants import PLACEHOLDER_TEXT, THREAD_CONTEXT_MAX_PAGES
from app.graph.client import GraphError, graph_client
from app.utils.helpers import extract_text_from_card

logger = logging.getLogger("gti-teams-bot")

_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_BLANK_LINES_RE = re.compile(r"\n{2,}")
_THREAD_ROOT_ID_RE = re.compile(r";messageid=(\d+)")


# ── Text extraction ──────────────────────────────────────────────────────────

def html_to_text(raw_html: str) -> str:
    """
    Extract plain text from a Teams message's HTML body.

    Args:
        raw_html: Raw HTML string from the message body.

    Returns:
        Cleaned plain-text string.
    """
    if not raw_html:
        return ""
    text = re.sub(r"(?i)</p>|<br\s*/?>", "\n", raw_html)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def collapse_blank_lines(text: str) -> str:
    """
    Collapse multiple consecutive blank lines into single newlines.

    Args:
        text: Input string with potential runs of blank lines.

    Returns:
        Cleaned text string.
    """
    return _BLANK_LINES_RE.sub("\n", text).strip()


def get_author(msg: dict[str, Any]) -> str:
    """
    Extract the display name of a message author (user or application).

    Args:
        msg: Graph message dictionary.

    Returns:
        Display name string, or 'Unknown' if not found.
    """
    frm = msg.get("from") or {}
    user_name = (frm.get("user") or {}).get("displayName")
    if user_name:
        return user_name
    app_name = (frm.get("application") or {}).get("displayName")
    if app_name:
        return app_name
    return "Unknown"


def extract_attachment_text(attachments: list[dict[str, Any]] | None) -> str:
    """
    Extract plain-text content from message Adaptive Card attachments.

    Args:
        attachments: List of attachment dictionaries from Microsoft Graph.

    Returns:
        Concatenated text extracted from all Adaptive Cards.
    """
    parts: list[str] = []
    for att in attachments or []:
        content_type = (att.get("contentType") or "").lower()
        raw_content = att.get("content")
        if not raw_content or "card.adaptive" not in content_type:
            continue
        try:
            card = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        except json.JSONDecodeError:
            continue
        card_text = extract_text_from_card(card)
        if card_text:
            parts.append(card_text)
    return "\n\n".join(parts)


# ── Bot's-own-placeholder exclusion ─────────────────────────────────────────

def is_placeholder_message(msg: dict[str, Any], bot_app_id: str) -> bool:
    """
    Determine whether a message is the bot's own pending placeholder message.

    Args:
        msg: Graph message dictionary.
        bot_app_id: Configured Microsoft application/client ID for the bot.

    Returns:
        True if the message matches the bot's identity and placeholder text.
    """
    frm = msg.get("from") or {}
    application = frm.get("application") or {}
    app_id = application.get("id")
    is_from_user = frm.get("user") is not None

    body_content = (msg.get("body") or {}).get("content", "") or ""
    has_placeholder_text = PLACEHOLDER_TEXT in body_content
    if not has_placeholder_text:
        has_placeholder_text = PLACEHOLDER_TEXT in extract_attachment_text(msg.get("attachments"))
    if not has_placeholder_text:
        return False

    if bot_app_id and app_id:
        return app_id == bot_app_id

    return not is_from_user


# ── Graph fetch ──────────────────────────────────────────────────────────────

def _parse_graph_datetime(value: str) -> Optional[datetime]:
    """
    Parse an ISO 8601 timestamp string from Microsoft Graph.

    Args:
        value: Timestamp string.

    Returns:
        datetime object if parsing succeeds, or None.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


_CLOCK_SKEW_MARGIN = timedelta(seconds=60)


def _detect_page_order(page: list[dict[str, Any]]) -> Optional[str]:
    """
    Detect whether messages in a Graph page are ordered ascending or descending.

    Args:
        page: List of message dictionaries in the current page.

    Returns:
        'asc', 'desc', or None if order cannot be determined.
    """
    if len(page) < 2:
        return None
    first = _parse_graph_datetime(page[0].get("createdDateTime") or "")
    last = _parse_graph_datetime(page[-1].get("createdDateTime") or "")
    if first is None or last is None:
        return None
    return "asc" if last >= first else "desc"


def _page_reaches_target(page: list[dict[str, Any]], order: Optional[str], target: Optional[datetime]) -> bool:
    """
    Check if a fetched message page has reached or passed the target timestamp.

    Args:
        page: List of message dictionaries in the page.
        order: Page ordering ('asc' or 'desc').
        target: Target timestamp to check against.

    Returns:
        True if the page spans beyond the target timestamp.
    """
    if target is None or not page or order is None:
        return False
    if order == "desc":
        oldest_in_page = _parse_graph_datetime(page[-1].get("createdDateTime") or "")
        return oldest_in_page is not None and oldest_in_page <= target - _CLOCK_SKEW_MARGIN
    newest_in_page = _parse_graph_datetime(page[-1].get("createdDateTime") or "")
    return newest_in_page is not None and newest_in_page >= target + _CLOCK_SKEW_MARGIN


def fetch_thread_messages(
    team_id: str,
    channel_id: str,
    thread_id: str,
    limit: int = 5,
    exclude_message_id: str = "",
    bot_app_id: str = "",
    target_timestamp: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """
    Fetch recent prior messages in a channel thread via Microsoft Graph.

    Args:
        team_id: Teams team identifier.
        channel_id: Teams channel identifier.
        thread_id: Root post ID of the thread.
        limit: Maximum number of prior messages to return.
        exclude_message_id: Activity ID of the triggering message to omit.
        bot_app_id: Client ID of the bot for placeholder filtering.
        target_timestamp: Optional timestamp of the triggering activity.

    Returns:
        List of message dictionaries with 'author', 'text', and 'id' fields.
    """
    messages: list[dict[str, Any]] = []

    root_url = f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}"
    root_resp = graph_client.get(root_url)
    if root_resp.status_code == 200:
        messages.append(root_resp.json())
    elif root_resp.status_code == 404:
        logger.warning("[GRAPH] Thread root message not found (team=%s channel=%s thread=%s)", team_id, channel_id, thread_id)
    else:
        raise GraphError(f"Graph root message fetch failed ({root_resp.status_code}): {root_resp.text}")

    replies_url: Optional[str] = (
        f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}/replies?$top=50"
    )
    # THREAD_CONTEXT_MAX_PAGES is a safety ceiling against runaway
    # pagination, not the primary stopping condition — see target_timestamp above.
    pages_fetched = 0
    order: Optional[str] = None
    while replies_url and pages_fetched < THREAD_CONTEXT_MAX_PAGES:
        resp = graph_client.get(replies_url)
        if resp.status_code != 200:
            raise GraphError(f"Graph replies fetch failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        page = payload.get("value", [])
        messages.extend(page)
        replies_url = payload.get("@odata.nextLink")
        pages_fetched += 1
        order = _detect_page_order(page) or order
        if _page_reaches_target(page, order, target_timestamp):
            break

    # A plain string sort on createdDateTime is unsafe: Graph omits the
    # fractional-seconds component when it's exactly zero, so e.g.
    # "...10:00:00.500Z" sorts before "...10:00:00Z" lexicographically
    # ('.' < 'Z') even though the latter is earlier — inverting order for
    # any two messages within the same whole second. Parse before comparing.
    messages.sort(
        key=lambda m: _parse_graph_datetime(m.get("createdDateTime") or "") or datetime.min.replace(tzinfo=timezone.utc)
    )
    messages = [
        m for m in messages
        if m.get("id") != exclude_message_id and not is_placeholder_message(m, bot_app_id)
    ]

    result = []
    for msg in messages[-limit:]:
        author = get_author(msg)
        body_content = (msg.get("body") or {}).get("content", "")
        text = html_to_text(body_content)
        if not text:
            text = extract_attachment_text(msg.get("attachments"))
        if text:
            text = collapse_blank_lines(text)
            result.append({"author": author, "text": text, "id": msg.get("id", "")})
    return result


def format_thread_context(messages: list[dict[str, Any]]) -> str:
    """
    Render fetched thread messages as a plain-text transcript block.

    Args:
        messages: List of message dictionaries with 'author' and 'text'.

    Returns:
        Multi-line plain-text transcript string.
    """
    return "\n".join(f"{msg['author']}: {msg['text']}" for msg in messages)


# ── Thread id parsing ────────────────────────────────────────────────────────

def get_thread_root_id(conversation_id: str) -> str:
    """
    Extract the channel thread's root message ID from a Teams conversation ID string.

    Args:
        conversation_id: Teams conversation ID (e.g. '19:...;messageid=123').

    Returns:
        Extracted root message ID string, or empty string if not present.
    """
    match = _THREAD_ROOT_ID_RE.search(conversation_id or "")
    return match.group(1) if match else ""


def get_team_post_id(activity) -> str:
    """
    Determine the channel thread's root Post ID for session storage and message retrieval.

    Args:
        activity: Inbound activity object.

    Returns:
        Root post ID string.
    """
    thread_id = get_thread_root_id(activity.conversation.id)
    if thread_id:
        return thread_id
    return getattr(activity, "id", None) or ""


def get_team_id(activity) -> str:
    """
    Extract the Microsoft Graph team identifier (AAD group ID) for this activity.

    Args:
        activity: Inbound activity object.

    Returns:
        Team ID string, or empty string if outside channels.
    """
    team = getattr(activity, "team", None)
    if not team:
        return ""
    return getattr(team, "aad_group_id", None) or getattr(team, "id", None) or ""


def get_channel_id(activity) -> str:
    """
    Extract the Teams channel ID for this activity.

    Falls back to parsing from conversation.id if channel data is missing.

    Args:
        activity: Inbound activity object.

    Returns:
        Channel ID string, or empty string if outside channels.
    """
    channel = getattr(activity, "channel", None)
    channel_id = (getattr(channel, "id", None) if channel else None) or ""
    if channel_id:
        return channel_id
    conv_id = getattr(getattr(activity, "conversation", None), "id", "") or ""
    if conv_id.startswith("19:") and "@thread." in conv_id:
        return conv_id.split(";")[0]
    return ""


# ── Orchestration ────────────────────────────────────────────────────────────

def get_thread_context(activity, scope: str) -> str:
    """
    Fetch and format the last N messages in a channel thread via Microsoft Graph.

    Args:
        activity: Inbound activity object.
        scope: Conversation scope ('channel', 'personal', etc.).

    Returns:
        Formatted transcript string, or empty string if not applicable or failed.
    """
    if scope != "channel" or not settings.thread_context_enabled:
        return ""

    team_id = get_team_id(activity)
    channel_id = get_channel_id(activity)
    # get_team_post_id(), not the raw get_thread_root_id(activity.conversation.id)
    # — a reply's conversation.id carries the root id directly, but the
    # thread's own opening post has no such suffix on ITS OWN conversation.id
    # at all. Without get_team_post_id()'s root-post fallback (its own id IS
    # the thread root id), thread context silently came back empty for the
    # very first message of every new channel thread — confirmed live.
    thread_id = get_team_post_id(activity)

    if not (team_id and channel_id and thread_id):
        return ""

    try:
        messages = fetch_thread_messages(
            team_id, channel_id, thread_id,
            limit=settings.thread_context_message_count,
            exclude_message_id=activity.id or "",
            bot_app_id=settings.client_id,
            target_timestamp=getattr(activity, "timestamp", None),
        )
        # Count only — never the messages' authors or text, which is exactly
        # what this context is: other people's conversation content.
        logger.info(
            "[GRAPH] Fetched %d thread message(s) | team=%s channel=%s thread=%s",
            len(messages), team_id, channel_id, thread_id,
        )
        return format_thread_context(messages)
    except GraphError as exc:
        logger.warning("[GRAPH] Thread context fetch failed: %s", exc)
        return ""
    except Exception:
        logger.exception("[GRAPH] Unexpected error fetching thread context.")
        return ""
