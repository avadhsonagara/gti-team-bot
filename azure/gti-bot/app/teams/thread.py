"""
Everything related to Microsoft Teams channel thread context lives here:
Graph API message fetching, HTML/Adaptive Card text extraction, thread-root
id parsing, the plain-text history log file, and the top-level orchestrator
(get_thread_context) called from the message handler.

Requires the bot's Entra app registration (CLIENT_ID/CLIENT_SECRET/TENANT_ID)
to be granted the Graph application permission `ChannelMessage.Read.All` with
tenant-admin consent — separate from the Bot Framework permissions the app
already uses to send/receive messages.

Channel-only: Teams has no thread/reply-chain concept for personal (1:1) or
group chats. Reading those would need the broader `Chat.Read.All` permission
instead, which this module does not use.
"""
import html as html_lib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import orjson

from app.config import settings
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
            card = orjson.loads(raw_content) if isinstance(raw_content, str) else raw_content
        except orjson.JSONDecodeError:
            continue
        card_text = extract_text_from_card(card)
        if card_text:
            parts.append(card_text)
    return "\n\n".join(parts)


# ── Graph fetch ──────────────────────────────────────────────────────────────

async def fetch_thread_messages(
    team_id: str,
    channel_id: str,
    thread_id: str,
    limit: int = 5,
    exclude_message_id: str = "",
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
    token = await graph_client._get_token()
    client = await graph_client._get_client()
    headers = {"Authorization": f"Bearer {token}"}

    messages: list[dict[str, Any]] = []

    root_url = f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}"
    root_resp = await client.get(root_url, headers=headers)
    if root_resp.status_code == 200:
        messages.append(root_resp.json())
    elif root_resp.status_code == 404:
        logger.warning("[GRAPH] Thread root message not found (team=%s channel=%s thread=%s)", team_id, channel_id, thread_id)
    else:
        raise GraphError(f"Graph root message fetch failed ({root_resp.status_code}): {root_resp.text}")

    replies_url: Optional[str] = (
        f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}/replies?$top=50"
    )
    # Cap pagination — a channel thread context window only needs the tail.
    pages_fetched = 0
    while replies_url and pages_fetched < 5:
        resp = await client.get(replies_url, headers=headers)
        if resp.status_code != 200:
            raise GraphError(f"Graph replies fetch failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        messages.extend(payload.get("value", []))
        replies_url = payload.get("@odata.nextLink")
        pages_fetched += 1

    messages.sort(key=lambda m: m.get("createdDateTime") or "")
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
    """
    if scope == "channel":
        thread_id = get_thread_root_id(activity.conversation.id)
        if thread_id:
            return thread_id
    return activity.conversation.id or ""


def get_team_id(activity) -> str:
    """Return the Graph-compatible team id (AAD group id) for this activity, or "" outside channels."""
    team = getattr(activity, "team", None)
    if not team:
        return ""
    return getattr(team, "aad_group_id", None) or getattr(team, "id", None) or ""


# ── Logging ──────────────────────────────────────────────────────────────────

def write_thread_context_log(conversation_id: str, thread_id: str, messages: list[dict]) -> None:
    """
    Append the raw fetched thread messages to a plain-text file (separate from
    the main server log) so thread history can be inspected on its own.
    Best-effort — a logging failure must never break message handling.

    This writes to local container disk, which is ephemeral and per-instance
    on Cloud Run — useful for local dev / a quick look on a single running
    instance, but not a durable or cross-instance log. The real audit trail
    is the [GRAPH] logger.info() calls below, which reach Cloud Logging.
    """
    if not settings.thread_context_log_file:
        return
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "=" * 80,
            f"{timestamp} | conversation={conversation_id} | thread={thread_id}",
            "=" * 80,
        ]
        if messages:
            for i, msg in enumerate(messages, start=1):
                lines.append(f"{i}. {msg['author']}: {msg['text']}")
        else:
            lines.append("(no messages fetched)")
        lines.append("")  # blank line between entries
        with open(settings.thread_context_log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        logger.exception("[GRAPH] Failed to write thread context log file.")


# ── Orchestration ────────────────────────────────────────────────────────────

async def get_thread_context(activity, scope: str) -> str:
    """
    Best-effort fetch of the last N messages in this channel's thread via
    Microsoft Graph, formatted as a plain-text transcript. Returns "" when
    thread context isn't applicable (not a channel) or on any failure —
    losing context is far less harmful than failing the whole request over it.
    """
    if scope != "channel" or not settings.thread_context_enabled:
        return ""

    team = getattr(activity, "team", None)
    channel = getattr(activity, "channel", None)
    team_id = getattr(team, "aad_group_id", None) or getattr(team, "id", None) if team else None
    channel_id = getattr(channel, "id", None) if channel else None
    thread_id = get_thread_root_id(activity.conversation.id)

    if not (team_id and channel_id and thread_id):
        return ""

    try:
        messages = await fetch_thread_messages(
            team_id, channel_id, thread_id,
            limit=settings.thread_context_message_count,
            exclude_message_id=activity.id or "",
        )
        logger.info(
            "[GRAPH] Fetched %d thread message(s) | team=%s channel=%s thread=%s",
            len(messages), team_id, channel_id, thread_id,
        )
        for i, msg in enumerate(messages, start=1):
            preview = msg["text"][:200] + ("..." if len(msg["text"]) > 200 else "")
            logger.info("[GRAPH]   %d. %s: %r", i, msg["author"], preview)
        write_thread_context_log(activity.conversation.id, thread_id, messages)
        return format_thread_context(messages)
    except GraphError as exc:
        logger.warning("[GRAPH] Thread context fetch failed: %s", exc)
        return ""
    except Exception:
        logger.exception("[GRAPH] Unexpected error fetching thread context.")
        return ""
