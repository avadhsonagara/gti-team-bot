"""
Everything related to Microsoft Teams channel thread context lives here:
Graph API message fetching, HTML/Adaptive Card text extraction, thread-root
id parsing, and the top-level orchestrator (get_thread_context) called from
the message handler.

Requires the bot's Entra app registration (CLIENT_ID/CLIENT_SECRET/TENANT_ID)
to be granted the Graph application permission `ChannelMessage.Read.All` with
tenant-admin consent — separate from the Bot Framework permissions the app
already uses to send/receive messages.

Channel-only: Teams has no thread/reply-chain concept for personal (1:1) or
group chats. Reading those would need the broader `Chat.Read.All` permission
instead, which this module does not use.
"""
import html as html_lib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings
from app.graph.client import GraphError, graph_client
from app.gti.session_store import get_team_id_for_channel
from app.utils.helpers import extract_text_from_card

logger = logging.getLogger("gti-teams-bot")

_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_BLANK_LINES_RE = re.compile(r"\n{2,}")
_THREAD_ROOT_ID_RE = re.compile(r";messageid=(\d+)")


# ── Text extraction ──────────────────────────────────────────────────────────

def html_to_text(raw_html: str) -> str:
    """Best-effort plain-text extraction from a Teams message's HTML body."""
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
    Collapse runs of blank lines down to a single newline, for compact thread
    history entries. extract_text_from_card() joins Adaptive Card fields with
    "\n\n" for readability when delivering a message to Teams — appropriate
    there, but it spreads a single alert card across dozens of lines once
    that text is reused as thread history.
    """
    return _BLANK_LINES_RE.sub("\n", text).strip()


def get_author(msg: dict[str, Any]) -> str:
    """Return the display name of a Graph channel message's sender (user or bot/app)."""
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
    Best-effort plain-text extraction from a message's attachments. Card-only
    messages (e.g. an Adaptive Card alert) have an empty `body.content` — the
    real content lives in `attachments[].content`, a JSON-encoded card.
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


# ── Graph fetch ──────────────────────────────────────────────────────────────

def _parse_graph_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


_CLOCK_SKEW_MARGIN = timedelta(seconds=60)
# Ceiling on paginated Graph /replies fetches when building thread context.
# The primary stopping condition is still timestamp-based (see
# _page_reaches_target/target_timestamp below) — most threads exit in 1-2
# pages, well under this cap. This value bounds the worst case instead: at
# $top=50 per page, 5 pages is at most 6 sequential Graph calls (root + 5
# reply pages), capping added latency on a single query at a few seconds.
# The trade-off is that a thread with more than ~250 replies since its last
# message near "now" can hit this cap before the timestamp check fires,
# truncating context to the oldest 250 replies instead of the most recent
# ones — accepted here in exchange for a tighter, predictable latency bound.
_THREAD_CONTEXT_MAX_PAGES = 5


def _detect_page_order(page: list[dict[str, Any]]) -> Optional[str]:
    """Detect whether messages in a Graph page are ordered ascending or descending."""
    if len(page) < 2:
        return None
    first = _parse_graph_datetime(page[0].get("createdDateTime") or "")
    last = _parse_graph_datetime(page[-1].get("createdDateTime") or "")
    if first is None or last is None:
        return None
    return "asc" if last >= first else "desc"


def _page_reaches_target(page: list[dict[str, Any]], order: Optional[str], target: Optional[datetime]) -> bool:
    """Check whether a fetched message page has reached or passed the target timestamp."""
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
    target_timestamp: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """
    Return up to `limit` most recent PRIOR messages (root + replies) in a
    channel thread, oldest first, as
    [{"author": str, "text": str, "id": str}, ...].

    `exclude_message_id` (the message that triggered this query) is dropped
    before the last-`limit` slice is taken — it's the current query, not
    thread history, and must not count against or appear in the N latest
    messages. The root only shows up if it falls within the last `limit`
    messages by time — on a thread with more than `limit` other replies, it
    drops off just like any older message.
    """
    token = graph_client._get_token()
    session = graph_client._get_session()
    headers = {"Authorization": f"Bearer {token}"}

    messages: list[dict[str, Any]] = []

    root_url = f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}"
    root_resp = session.get(root_url, headers=headers, timeout=graph_client.timeout)
    if root_resp.status_code == 200:
        messages.append(root_resp.json())
    elif root_resp.status_code == 404:
        logger.warning("[GRAPH] Thread root message not found (team=%s channel=%s thread=%s)", team_id, channel_id, thread_id)
    else:
        raise GraphError(f"Graph root message fetch failed ({root_resp.status_code}): {root_resp.text}")

    replies_url: Optional[str] = (
        f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}/replies?$top=50"
    )
    # _THREAD_CONTEXT_MAX_PAGES is a safety ceiling against runaway
    # pagination, not the primary stopping condition — see target_timestamp
    # above. On a thread with more replies than this ceiling covers,
    # target_timestamp-based early-stop (via _page_reaches_target) is what
    # actually keeps this from stopping short of the present.
    pages_fetched = 0
    order: Optional[str] = None
    while replies_url and pages_fetched < _THREAD_CONTEXT_MAX_PAGES:
        resp = session.get(replies_url, headers=headers, timeout=graph_client.timeout)
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
    if exclude_message_id:
        messages = [m for m in messages if m.get("id") != exclude_message_id]

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
    """Render fetched thread messages as a plain-text transcript block."""
    return "\n".join(f"{msg['author']}: {msg['text']}" for msg in messages)


# ── Thread id parsing ────────────────────────────────────────────────────────

def get_thread_root_id(conversation_id: str) -> str:
    """
    Extract the channel thread's root message id from a Teams conversation id
    (e.g. "19:xxx@thread.tacv2;messageid=1234567890" -> "1234567890").
    Empty when the activity isn't a channel message (no thread concept exists
    for personal/group chats).
    """
    match = _THREAD_ROOT_ID_RE.search(conversation_id or "")
    return match.group(1) if match else ""


def get_session_key(activity, scope: str) -> str:
    """
    Return the key used to persist/look up the GTI session_id for this
    conversation: the channel thread's root Post ID when available, otherwise
    the Conversation ID (personal/group chats, or a channel message that
    isn't part of a thread yet).

    A brand-new top-level channel post has no ";messageid=" in its own
    conversation.id (only replies get that) — but the post's own
    `activity.id` IS that root id, so it's used as the fallback instead of
    the raw conversation.id, keeping the key stable once replies arrive.
    """
    if scope == "channel":
        thread_id = get_thread_root_id(activity.conversation.id)
        if thread_id:
            return thread_id
        if getattr(activity, "id", None):
            return activity.id
    return activity.conversation.id or ""


def get_team_id(activity) -> str:
    """Return the Graph-compatible team id (AAD group id) for this activity, or "" outside channels."""
    team = getattr(activity, "team", None)
    if not team:
        return ""
    return getattr(team, "aad_group_id", None) or getattr(team, "id", None) or ""


def get_channel_id(activity) -> str:
    """
    Return the Teams channel id for this activity, or "" outside channels.

    Falls back to parsing it from conversation.id when channelData.channel
    is missing (Teams doesn't always populate it, e.g. on some mobile clients).
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
    Best-effort fetch of the last N messages in this channel's thread via
    Microsoft Graph, formatted as a plain-text transcript. Returns "" when
    thread context isn't applicable (not a channel) or on any failure —
    losing context is far less harmful than failing the whole request over it.
    """
    if scope != "channel":
        return ""

    # Graph's message-listing endpoint needs team_id in the URL; if this
    # activity's own channelData.team is missing, fall back to a team_id
    # cached from an earlier message in the same channel.
    team_id = get_team_id(activity)
    channel_id = get_channel_id(activity)
    if not team_id and channel_id:
        team_id = get_team_id_for_channel(channel_id) or ""
    thread_id = get_thread_root_id(activity.conversation.id)

    if not (team_id and channel_id and thread_id):
        return ""

    try:
        messages = fetch_thread_messages(
            team_id, channel_id, thread_id,
            limit=settings.thread_context_message_count,
            exclude_message_id=activity.id or "",
            target_timestamp=getattr(activity, "timestamp", None),
        )
        logger.info(
            "[GRAPH] Fetched %d thread message(s) | team=%s channel=%s thread=%s",
            len(messages), team_id, channel_id, thread_id,
        )
        for i, msg in enumerate(messages, start=1):
            preview = msg["text"][:200] + ("..." if len(msg["text"]) > 200 else "")
            logger.info("[GRAPH]   %d. %s: %r", i, msg["author"], preview)
        return format_thread_context(messages)
    except GraphError as exc:
        logger.warning("[GRAPH] Thread context fetch failed: %s", exc)
        return ""
    except Exception:
        logger.exception("[GRAPH] Unexpected error fetching thread context.")
        return ""
