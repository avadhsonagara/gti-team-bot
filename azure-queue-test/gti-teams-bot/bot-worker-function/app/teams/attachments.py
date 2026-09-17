"""
Downloading file/image attachments a user shared with the bot in a Teams
message, so they can be forwarded to the GTI Agentic API as multipart
artifacts (see gti.client.GTIAgenticClient.send_message(..., files=...)).

Two download paths, tried per attachment, then a Microsoft Graph fallback
(channel and group chat):
  1. "application/vnd.microsoft.teams.file.download.info" (personal 1:1 direct
     upload): bytes live behind a pre-signed `content["downloadUrl"]` needing
     no bot credentials.
  2. Bot Framework Connector `content_url` (e.g. inline/pasted images,
     `image/*`, and direct file uploads in personal chats): bytes live
     behind the Connector endpoint, requiring the bot's own token
     (app/teams/bot_client.get_bot_token).
  3. Microsoft Graph fallback (channel and group chat): confirmed live that
     Bot Framework does NOT include a usable content_url on a channel OR
     group chat message at all — not just for a user picking an EXISTING
     SharePoint/OneDrive file, but for a freshly-uploaded/pasted one too. The
     only attachment Bot Framework gives the bot in either case is its own
     `text/html` rendering of the message text (confirmed live it carries no
     marker distinguishing "has a real file" from plain text either way).
     The real reference only exists in Microsoft Graph's copy of the same
     message, as a chatMessageAttachment with a contentUrl pointing at the
     SharePoint (a channel's own document library) or OneDrive (a group
     chat sender's own "Microsoft Teams Chat Files" folder) item.

     Bot Framework's `activity.id` IS the same as Graph's `chatMessage.id`
     for both channel and group chat messages — confirmed live for a
     channel thread's root post, a channel reply, and a group chat message
     — so this fetches that exact message directly by id
     (_get_channel_message_by_id()/_get_chat_message_by_id()) rather than
     listing recent messages and guessing by sender + closest timestamp.
     That guessing approach used to be the only option here and had a real
     correctness bug: a message with NO real attachment (Bot Framework's
     `text/html` rendering is present regardless) could still match some
     unrelated older message that happened to have one, silently attaching
     the wrong file. Once the reference is found, the actual bytes come
     from the Graph Shares API: the contentUrl is encoded into a share token
     (base64 -> unpadded base64url -> "u!" prefix), then GET
     /shares/{token}/driveItem/content downloads the file.

Uses `requests`, same as this app's other HTTP clients (gti/client.py,
graph/client.py) — entirely synchronous, including the bot's own token
needed for the content_url path.
"""
import base64
import logging
from typing import Any, Optional

import requests

from app.constants import ATTACHMENT_DOWNLOAD_TIMEOUT
from app.graph.client import GraphError, graph_client
from app.teams.bot_client import get_bot_token
from app.teams.thread import get_channel_id, get_team_id, get_team_post_id

logger = logging.getLogger("gti-teams-bot")

_FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"
_NON_FILE_PREFIXES = ("text/html", "application/vnd.microsoft.card.")
_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def _is_user_file(content_type: str) -> bool:
    if not content_type:
        return False
    return not content_type.startswith(_NON_FILE_PREFIXES)


def _download(url: str, headers: Optional[dict[str, str]] = None) -> bytes:
    """Blocking GET."""
    resp = requests.get(url, headers=headers, timeout=ATTACHMENT_DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    return resp.content


# ── Microsoft Graph fallback (channel and group chat) ───────────────────────

def _share_token(content_url: str) -> str:
    """contentUrl -> Graph Shares API token: base64, then unpadded base64url, prefixed 'u!'."""
    raw = base64.b64encode(content_url.encode("utf-8")).decode("ascii")
    return "u!" + raw.rstrip("=").replace("+", "-").replace("/", "_")


def _download_graph_share(content_url: str) -> bytes:
    """
    Resolve a SharePoint/OneDrive contentUrl to bytes via the Graph Shares
    API. Uses graph_client's own default timeout, like every other Graph
    call, rather than ATTACHMENT_DOWNLOAD_TIMEOUT — this is a Graph API call,
    not a Bot Framework Connector download.
    """
    url = f"{_GRAPH_BASE_URL}/shares/{_share_token(content_url)}/driveItem/content"
    resp = graph_client.get(url)
    resp.raise_for_status()
    return resp.content


def _get_channel_message_by_id(
    team_id: str, channel_id: str, thread_root_id: str, message_id: str,
) -> Optional[dict[str, Any]]:
    """
    Direct Graph GET for one exact channel message — the thread's root
    itself when message_id == thread_root_id, otherwise a reply within it.
    Confirmed live that Bot Framework's activity.id equals the Graph
    chatMessage.id in both cases, so no listing or fuzzy matching is needed.
    """
    if message_id == thread_root_id:
        url = f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{message_id}"
    else:
        url = f"{_GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{thread_root_id}/replies/{message_id}"
    resp = graph_client.get(url)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise GraphError(f"Graph message-by-id fetch failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _get_chat_message_by_id(chat_id: str, message_id: str) -> Optional[dict[str, Any]]:
    """
    Direct Graph GET for one exact group chat message. Confirmed live that
    Bot Framework's activity.id equals the Graph chatMessage.id here too, so
    no listing or fuzzy matching is needed — same as channel, just without a
    thread root/reply distinction (chats have no reply-chain concept).
    """
    url = f"{_GRAPH_BASE_URL}/chats/{chat_id}/messages/{message_id}"
    resp = graph_client.get(url)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise GraphError(f"Graph message-by-id fetch failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _fetch_graph_message_attachments(activity, scope: str) -> list[dict[str, Any]]:
    """
    Best-effort: find the inbound message via Microsoft Graph and return its
    attachments[]. Channel and groupChat only — personal chats already get
    real file data straight from the Bot Framework activity.

    activity.id IS the Graph chatMessage.id for both scopes (confirmed
    live: a channel thread's root post, a channel reply, and a group chat
    message) — this fetches that exact message directly by id, never a
    listing or a sender/timestamp guess. The old list-and-guess approach
    (used before this fix) had a real bug: a message with NO actual
    attachment (Bot Framework's own text/html rendering is present
    regardless of whether a file was attached) could still match some
    unrelated older message that happened to have one, silently attaching
    the wrong file.
    """
    if scope not in ("channel", "groupChat"):
        return []

    conv_id = getattr(getattr(activity, "conversation", None), "id", None)
    if not conv_id:
        return []

    message_id = getattr(activity, "id", None) or ""
    if not message_id:
        return []

    try:
        if scope == "channel":
            team_id = get_team_id(activity)
            channel_id = get_channel_id(activity)
            # get_team_post_id(), not get_thread_root_id(conv_id) directly —
            # see thread.py's get_thread_context for why: a thread's own
            # opening post has no ";messageid=" suffix on its OWN
            # conversation.id, so the raw parse comes back empty for it.
            thread_root_id = get_team_post_id(activity)
            if not (team_id and channel_id and thread_root_id):
                return []
            match = _get_channel_message_by_id(team_id, channel_id, thread_root_id, message_id)
        else:  # groupChat
            match = _get_chat_message_by_id(conv_id, message_id)

        if not match:
            logger.warning("[ATTACHMENT] Graph fallback found no matching message (scope=%s)", scope)
            return []
        return match.get("attachments") or []

    except GraphError as exc:
        logger.warning("[ATTACHMENT] Graph fallback failed: %s", exc)
        return []
    except Exception:
        logger.exception("[ATTACHMENT] Unexpected error in Graph attachment fallback")
        return []


# ── Public entry point ──────────────────────────────────────────────────────

def download_attachments(ctx) -> list[tuple[str, bytes, str]]:
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
            # Never logs `attachment.content` here — for content_type
            # "text/html" that content IS the message's own text (Teams'
            # own HTML rendering of it), not a real attachment.
            logger.info("[ATTACHMENT] Skipping non-file attachment (content_type=%r)", content_type)
            continue
        name = attachment.name or "file"

        try:
            if content_type == _FILE_DOWNLOAD_INFO:
                info = attachment.content or {}
                download_url = info.get("downloadUrl") if isinstance(info, dict) else None
                if not download_url:
                    logger.warning("[ATTACHMENT] %r has no downloadUrl — skipping", name)
                    continue
                data = _download(download_url)
                mime = "application/octet-stream"
            elif attachment.content_url:
                bot_token = get_bot_token()
                headers = {"Authorization": f"Bearer {bot_token}"} if bot_token else None
                data = _download(attachment.content_url, headers)
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
        graph_attachments = _fetch_graph_message_attachments(activity, scope)
        for g_att in graph_attachments:
            c_url = g_att.get("contentUrl")
            name = g_att.get("name") or "file"
            # Any attachment with a contentUrl is worth trying — not just
            # contentType == "reference" (Graph's marker for a user sharing
            # an EXISTING SharePoint/OneDrive file). A freshly-uploaded
            # channel file/image resolves through Graph the same way (Teams
            # stores every channel file in SharePoint either way, confirmed
            # live), and may not carry that exact contentType.
            if not c_url:
                continue

            try:
                data = _download_graph_share(c_url)
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

    if raw_attachments:
        logger.info("[ATTACHMENT] Downloaded %d of %d attachment(s)", len(results), len(raw_attachments))
    return results
