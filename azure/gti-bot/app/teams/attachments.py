"""
Downloading file/image attachments a user shared with the bot in a Teams
message, so they can be forwarded to the GTI Agentic API as multipart
artifacts (see gti.client.GTIAgenticClient.send_message(..., files=...)).

Three download paths, tried in order:
  1. "application/vnd.microsoft.teams.file.download.info" (personal 1:1 direct
     upload): bytes live behind a pre-signed `content["downloadUrl"]` needing
     no bot credentials.
  2. Bot Framework Connector `content_url` (e.g. inline/pasted images,
     `image/*`): bytes live behind the Connector endpoint, requiring the
     bot's own token (resolved via `ctx.api.http`).
  3. Microsoft Graph fallback (channel and groupChat only): Teams does not
     include a usable file reference in the Bot Framework activity at all
     when a user shares an EXISTING file in a channel or group chat — the
     only attachment on the activity is Teams' own `text/html` rendering of
     the message text. The real reference only exists in Microsoft Graph's
     copy of the same message, as a chatMessageAttachment with
     contentType="reference" and a contentUrl pointing at the SharePoint/
     OneDrive item. Bot Framework's `activity.id` is NOT documented to match
     Graph's `chatMessage.id` for inbound user messages, so this lists the
     chat/channel's recent messages (GET .../messages?$orderby=createdDateTime
     desc) and picks the one matching the sender + closest timestamp, rather
     than trusting a direct get-by-id. Once the reference is found, the
     actual bytes come from the Graph Shares API: the contentUrl is encoded
     into a share token (base64 -> unpadded base64url -> "u!" prefix), then
     GET /shares/{token}/driveItem/content downloads the file.

Uses `requests`, same as this app's other HTTP clients (gti/client.py,
graph/client.py). requests is synchronous, so each network call runs via
asyncio.to_thread() to avoid blocking the single shared event loop.
"""
import asyncio
import base64
import logging
from datetime import datetime
from typing import Any, Optional

import requests

from app.graph.client import GraphError, graph_client
from app.teams.thread import get_thread_root_id

logger = logging.getLogger("gti-teams-bot")

_FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"
_NON_FILE_PREFIXES = ("text/html", "application/vnd.microsoft.card.")
_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_DOWNLOAD_TIMEOUT = (10.0, 30.0)  # (connect, read) seconds

# Graph message-listing/matching for the fallback path.
_MESSAGE_LIST_MAX_PAGES = 3       # $top=50/page — page 1 covers virtually every real case
_MESSAGE_MATCH_WINDOW_SECONDS = 120.0


def _is_user_file(content_type: str) -> bool:
    if not content_type:
        return False
    return not content_type.startswith(_NON_FILE_PREFIXES)


def _download(url: str, headers: Optional[dict[str, str]] = None) -> bytes:
    """Blocking GET — always run via asyncio.to_thread(), never awaited directly."""
    resp = requests.get(url, headers=headers, timeout=_DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    return resp.content


# ── Microsoft Graph fallback (channel / groupChat) ──────────────────────────

def _share_token(content_url: str) -> str:
    """contentUrl -> Graph Shares API token: base64, then unpadded base64url, prefixed 'u!'."""
    raw = base64.b64encode(content_url.encode("utf-8")).decode("ascii")
    return "u!" + raw.rstrip("=").replace("+", "-").replace("/", "_")


async def _download_graph_share(content_url: str) -> bytes:
    """Resolve a SharePoint/OneDrive contentUrl to bytes via the Graph Shares API."""
    token = await graph_client._get_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_GRAPH_BASE_URL}/shares/{_share_token(content_url)}/driveItem/content"
    return await asyncio.to_thread(_download, url, headers)


def _parse_graph_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _select_matching_message(
    messages: list[dict[str, Any]],
    sender_aad_id: Optional[str],
    activity_timestamp: Optional[datetime],
    window_seconds: float,
) -> Optional[dict[str, Any]]:
    """
    Pick the chatMessage that best matches the inbound activity: has
    attachments, same sender (when known), createdDateTime closest to (and
    within window_seconds of) the activity's own timestamp.
    """
    best, best_delta = None, None
    for msg in messages:
        if not msg.get("attachments"):
            continue
        frm_user_id = ((msg.get("from") or {}).get("user") or {}).get("id")
        if sender_aad_id and frm_user_id and frm_user_id != sender_aad_id:
            continue
        created = _parse_graph_datetime(msg.get("createdDateTime") or "")
        if created is None:
            continue
        delta = abs((activity_timestamp - created).total_seconds()) if activity_timestamp else 0.0
        if delta > window_seconds:
            continue
        if best is None or delta < best_delta:
            best, best_delta = msg, delta
    return best


async def _list_graph_chat_messages(chat_id: str, window_seconds: float) -> list[dict[str, Any]]:
    """List a group chat's recent messages via Graph, newest first."""
    token = await graph_client._get_token()
    session = graph_client._get_session()
    headers = {"Authorization": f"Bearer {token}"}

    messages: list[dict[str, Any]] = []
    url = f"{_GRAPH_BASE_URL}/chats/{chat_id}/messages?$top=50&$orderby=createdDateTime desc"
    pages_fetched = 0
    while url and pages_fetched < _MESSAGE_LIST_MAX_PAGES:
        resp = await asyncio.to_thread(session.get, url, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                "[ATTACHMENT] Graph chat-messages list failed (%d) for chat=%s: %s",
                resp.status_code, chat_id, resp.text,
            )
            break
        payload = resp.json()
        page = payload.get("value") or []
        messages.extend(page)
        oldest_on_page = _parse_graph_datetime(min((m.get("createdDateTime") or "" for m in page), default=""))
        if oldest_on_page and (datetime.now(oldest_on_page.tzinfo) - oldest_on_page).total_seconds() > window_seconds:
            break  # createdDateTime-desc — nothing further back can still be in-window
        url = payload.get("@odata.nextLink")
        pages_fetched += 1
    return messages


async def _list_graph_channel_messages(team_id: str, channel_id: str, thread_id: str) -> list[dict[str, Any]]:
    """List a channel thread's root + reply messages via Graph, newest first."""
    token = await graph_client._get_token()
    session = graph_client._get_session()
    headers = {"Authorization": f"Bearer {token}"}

    messages: list[dict[str, Any]] = []
    root_url = f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}"
    root_resp = await asyncio.to_thread(session.get, root_url, headers=headers)
    if root_resp.status_code == 200:
        messages.append(root_resp.json())
    elif root_resp.status_code != 404:
        logger.warning("[ATTACHMENT] Graph channel root-message fetch failed (%d)", root_resp.status_code)

    url = (
        f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_id}"
        f"/replies?$top=50&$orderby=createdDateTime desc"
    )
    pages_fetched = 0
    while url and pages_fetched < _MESSAGE_LIST_MAX_PAGES:
        resp = await asyncio.to_thread(session.get, url, headers=headers)
        if resp.status_code != 200:
            logger.warning("[ATTACHMENT] Graph channel-replies list failed (%d)", resp.status_code)
            break
        payload = resp.json()
        messages.extend(payload.get("value") or [])
        url = payload.get("@odata.nextLink")
        pages_fetched += 1
    return messages


async def _fetch_graph_message_attachments(activity, scope: str) -> list[dict[str, Any]]:
    """
    Best-effort: find the inbound message via Microsoft Graph and return its
    attachments[]. Only applies to channel and groupChat — personal chats
    already get real file data straight from the Bot Framework activity.

    Deliberately does NOT do a direct get-by-id lookup: activity.id is not
    documented to equal Graph's chatMessage.id for inbound user messages, so
    a get-by-id would be as likely to 404 on a mismatch as to succeed. Instead
    this lists recent messages and matches by sender + closest timestamp.
    """
    if scope not in ("channel", "groupChat"):
        return []

    conv_id = getattr(getattr(activity, "conversation", None), "id", None)
    sender = getattr(activity, "from_property", None) or getattr(activity, "from_", None)
    sender_aad_id = getattr(sender, "aad_object_id", None) or None
    activity_timestamp = getattr(activity, "timestamp", None)
    if not conv_id:
        return []

    try:
        if scope == "channel":
            team = getattr(activity, "team", None)
            team_id = (getattr(team, "aad_group_id", None) or getattr(team, "id", None)) if team else None
            channel = getattr(activity, "channel", None)
            channel_id = getattr(channel, "id", None) if channel else None
            thread_id = get_thread_root_id(conv_id)
            if not (team_id and channel_id and thread_id):
                return []
            messages = await _list_graph_channel_messages(team_id, channel_id, thread_id)
        else:  # groupChat
            messages = await _list_graph_chat_messages(conv_id, _MESSAGE_MATCH_WINDOW_SECONDS)

        match = _select_matching_message(messages, sender_aad_id, activity_timestamp, _MESSAGE_MATCH_WINDOW_SECONDS)
        if not match:
            logger.warning(
                "[ATTACHMENT] Graph fallback found no matching message with attachments (scope=%s)", scope,
            )
            return []
        return match.get("attachments") or []

    except GraphError as exc:
        logger.warning("[ATTACHMENT] Graph fallback failed: %s", exc)
        return []
    except Exception:
        logger.exception("[ATTACHMENT] Unexpected error in Graph attachment fallback")
        return []


# ── Public entry point ──────────────────────────────────────────────────────

async def download_attachments(ctx) -> list[tuple[str, bytes, str]]:
    """
    Best-effort download of every user-shared file/image attachment on this
    message activity, as [(filename, bytes, content_type), ...].

    Returns [] when there are none, or on total failure — losing an
    attachment is far less harmful than failing the whole query over it.
    """
    activity = ctx.activity
    scope = getattr(activity.conversation, "conversation_type", "") or ""
    raw_attachments = getattr(activity, "attachments", None) or []
    logger.info("[ATTACHMENT] %d raw attachment(s) on this activity (scope=%s)", len(raw_attachments), scope)
    for a in raw_attachments:
        logger.info(
            "[ATTACHMENT] raw: content_type=%r name=%r has_content_url=%s has_content=%s",
            a.content_type, a.name, bool(a.content_url), bool(a.content),
        )

    results: list[tuple[str, bytes, str]] = []

    for attachment in raw_attachments:
        content_type = attachment.content_type or ""
        if not _is_user_file(content_type):
            logger.info(
                "[ATTACHMENT] Skipping non-file attachment (content_type=%r) content=%r",
                content_type, attachment.content,
            )
            continue
        name = attachment.name or "file"

        try:
            if content_type == _FILE_DOWNLOAD_INFO:
                info = attachment.content or {}
                download_url = info.get("downloadUrl") if isinstance(info, dict) else None
                if not download_url:
                    logger.warning("[ATTACHMENT] %r has no downloadUrl — skipping", name)
                    continue
                data = await asyncio.to_thread(_download, download_url)
                mime = "application/octet-stream"
            elif attachment.content_url:
                bot_token = await ctx.api.http._resolve_token(None)
                headers = {"Authorization": f"Bearer {bot_token}"} if bot_token else None
                data = await asyncio.to_thread(_download, attachment.content_url, headers)
                mime = content_type or "application/octet-stream"
            else:
                logger.warning(
                    "[ATTACHMENT] %r (content_type=%r) has neither downloadUrl nor content_url — skipping",
                    name, content_type,
                )
                continue

            logger.info("[ATTACHMENT] Downloaded %r (%d bytes, %s)", name, len(data), mime)
            results.append((name, data, mime))

        except requests.exceptions.RequestException as exc:
            # Deliberately not logging str(exc) / exc_info here — requests embeds the
            # full request URL in its error message, and for the downloadUrl path
            # that URL is itself a bearer-equivalent credential (pre-signed, no
            # separate auth needed). Logging it would leak that token.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "[ATTACHMENT] Download failed for %r (content_type=%s, status=%s): %s",
                name, content_type, status, type(exc).__name__,
            )

        except Exception:
            logger.exception("[ATTACHMENT] Unexpected error downloading %r (content_type=%s)", name, content_type)

    if not results and raw_attachments:
        graph_attachments = await _fetch_graph_message_attachments(activity, scope)
        for g_att in graph_attachments:
            c_type = (g_att.get("contentType") or "").lower()
            c_url = g_att.get("contentUrl")
            name = g_att.get("name") or "file"
            if c_type != "reference" or not c_url:
                continue

            try:
                data = await _download_graph_share(c_url)
                mime = "application/octet-stream"
                logger.info("[ATTACHMENT] Downloaded %r via Microsoft Graph (%d bytes, %s)", name, len(data), mime)
                results.append((name, data, mime))
            except requests.exceptions.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                logger.warning(
                    "[ATTACHMENT] Graph download failed for %r (status=%s): %s",
                    name, status, type(exc).__name__,
                )
            except Exception:
                logger.exception("[ATTACHMENT] Unexpected error downloading Graph attachment %r", name)

    return results
