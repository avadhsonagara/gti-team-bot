"""
Everything related to Microsoft Teams channel thread context lives here:
Graph API message fetching, HTML/Adaptive Card text extraction, thread-root
id parsing, the bot's-own-placeholder exclusion filter, and the top-level
orchestrator (get_thread_context) called from the job processor.

Requires the bot's Entra app registration (or Managed Identity) to be
granted the Graph application permission `ChannelMessage.Read.All` with
tenant-admin consent — separate from the Bot Framework permissions the app
already uses to send/receive messages.

Channel-only: Teams has no thread/reply-chain concept for personal (1:1) or
group chats. Reading those would need the broader `Chat.Read.All` permission
instead, which this module does not use.

── Why this module needs a placeholder-exclusion filter at all ─────────────
In the synchronous (non-queue) version of this bot, thread context was always
fetched BEFORE posting the "looking into that…" placeholder, so the
placeholder never had a chance to show up in Graph's message history. In this
queue architecture, the Ingest Function posts that placeholder immediately
and returns — by the time this Worker Function calls Graph (potentially
minutes later), the placeholder is already sitting in the channel as a real
message. Left unfiltered, every follow-up query in the same thread would feed
the model its own "⏳ Looking into that…" text back as if it were part of the
conversation. is_placeholder_message() below identifies and drops it.
"""
import html as html_lib
import json
import logging
import re
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


# ── Bot's-own-placeholder exclusion ─────────────────────────────────────────

def is_placeholder_message(msg: dict[str, Any], bot_app_id: str) -> bool:
    """
    True if this Graph channel message is this bot's own "looking into
    that…" placeholder (or final response) and should be excluded from
    thread context fed back into the GTI prompt.

    Two checks, in order of confidence — and, importantly, BOTH require the
    message to plausibly be from the bot, not just any message that happens
    to contain the placeholder text:

      1. `from.application.id` matches our own CLIENT_ID exactly — the
         strongest signal Graph gives us. When present, this alone decides
         it; the content check just confirms it's specifically the
         placeholder (not, say, a real final answer this bot posted earlier
         in the thread, which SHOULD stay in context).
      2. Graph sometimes omits `from.application.id` for a bot's own posts
         (observed to vary by tenant/Graph API version). As a fallback, a
         bot-posted message never has `from.user` set — a human sender
         always does — so "no user" is used as the identity signal instead,
         still combined with the content match.

    Deliberately does NOT match on placeholder text alone with no identity
    signal at all: a human could paste or quote that exact text, and
    excluding a real user's message from context because of that would be a
    false positive with no upside.
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

def fetch_thread_messages(
    team_id: str,
    channel_id: str,
    thread_id: str,
    limit: int = 5,
    exclude_message_id: str = "",
    bot_app_id: str = "",
) -> list[dict[str, Any]]:
    """
    Return up to `limit` most recent PRIOR messages (root + replies) in a
    channel thread, oldest first, as
    [{"author": str, "text": str, "id": str}, ...].

    `exclude_message_id` (the message that triggered this query) is dropped
    before the last-`limit` slice is taken — it's the current query, not
    thread history, and must not count against or appear in the N latest
    messages. This bot's own placeholder/status messages are also dropped via
    is_placeholder_message() — see this module's docstring for why that
    matters specifically in the queue architecture.
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

    # $orderby=createdDateTime desc (also used in attachments.py's own Graph
    # replies fetch) so the capped pagination below walks from the NEWEST
    # reply backwards. Without it, Graph's default ascending order means a
    # thread with more than 250 replies (5 pages x 50) would only ever see
    # its oldest 250 — silently returning stale context instead of the
    # actual most-recent messages. The final sort()+[-limit:] below still
    # re-orders these chronologically before slicing, so this only changes
    # WHICH replies get fetched, not how they're presented.
    replies_url: Optional[str] = (
        f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}"
        f"/replies?$top=50&$orderby=createdDateTime desc"
    )
    # Cap pagination — a channel thread context window only needs the tail.
    pages_fetched = 0
    while replies_url and pages_fetched < THREAD_CONTEXT_MAX_PAGES:
        resp = graph_client.get(replies_url)
        if resp.status_code != 200:
            raise GraphError(f"Graph replies fetch failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        messages.extend(payload.get("value", []))
        replies_url = payload.get("@odata.nextLink")
        pages_fetched += 1

    messages.sort(key=lambda m: m.get("createdDateTime") or "")
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


def get_team_post_id(activity) -> str:
    """
    Return the channel thread's root Post ID — used as the session table's
    RowKey. Channel-only; callers must not use this for personal/group
    chats, which have no thread concept and never persist a session.

    A reply's own conversation.id carries the root id directly
    (";messageid=<rootId>", extracted by get_thread_root_id()). The root
    post itself has no such suffix on ITS OWN conversation.id — but its own
    activity.id IS that root id, so it's used as the fallback. Without this
    fallback, the opening post of a new thread and its first reply would
    resolve to two different ids (the opening post's own conversation.id vs.
    the reply's parsed root id) and never find each other's stored session.
    """
    thread_id = get_thread_root_id(activity.conversation.id)
    if thread_id:
        return thread_id
    return getattr(activity, "id", None) or ""


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
    if scope != "channel" or not settings.thread_context_enabled:
        return ""

    team_id = get_team_id(activity)
    channel_id = get_channel_id(activity)
    thread_id = get_thread_root_id(activity.conversation.id)

    if not (team_id and channel_id and thread_id):
        return ""

    try:
        messages = fetch_thread_messages(
            team_id, channel_id, thread_id,
            limit=settings.thread_context_message_count,
            exclude_message_id=activity.id or "",
            bot_app_id=settings.client_id,
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
