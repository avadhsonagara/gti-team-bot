"""
Downloading file/image attachments a user shared with the bot in a Teams
message, so they can be forwarded to the GTI Agentic API as multipart
artifacts (see gti.client.GTIAgenticClient.send_message(..., files=...)).

Two download paths, tried per attachment:
  1. "application/vnd.microsoft.teams.file.download.info" (personal 1:1 direct
     upload): bytes live behind a pre-signed `content["downloadUrl"]` needing
     no bot credentials.
  2. Bot Framework Connector `content_url` (e.g. inline/pasted images,
     `image/*`, and direct file uploads in channel/group chats): bytes live
     behind the Connector endpoint, requiring the bot's own token
     (app/teams/bot_client.get_bot_token).

Neither path depends on conversation scope — a direct laptop upload works
the same way in personal (1:1), group, and channel chats.

Deliberately does NOT handle a user picking an EXISTING file from SharePoint/
OneDrive (the attachment picker's "OneDrive" / "Browse Teams Files" option,
as opposed to uploading straight from disk) — Teams gives the bot no usable
file reference at all for that case (only its own text/html rendering of the
message), and resolving it requires a separate Microsoft Graph lookup this
bot intentionally doesn't perform right now. Only directly-uploaded files are
fetched; an existing-file share is silently skipped, same as having no
attachment.

Uses `requests`, same as this app's other HTTP clients (gti/client.py,
graph/client.py) — entirely synchronous, including the bot's own token
needed for the content_url path.
"""
import logging
from typing import Optional

import requests

from app.constants import ATTACHMENT_DOWNLOAD_TIMEOUT
from app.teams.bot_client import get_bot_token

logger = logging.getLogger("gti-teams-bot")

_FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"
_NON_FILE_PREFIXES = ("text/html", "application/vnd.microsoft.card.")


def _is_user_file(content_type: str) -> bool:
    if not content_type:
        return False
    return not content_type.startswith(_NON_FILE_PREFIXES)


def _download(url: str, headers: Optional[dict[str, str]] = None) -> bytes:
    """Blocking GET."""
    resp = requests.get(url, headers=headers, timeout=ATTACHMENT_DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    return resp.content


# ── Public entry point ──────────────────────────────────────────────────────

def download_attachments(ctx) -> list[tuple[str, bytes, str]]:
    """
    Best-effort download of every user-uploaded file/image attachment on this
    message activity, as [(filename, bytes, content_type), ...]. Works the
    same way regardless of conversation scope (personal, group, or channel).

    Returns [] when there are none, or on total failure — losing an
    attachment is far less harmful than failing the whole query over it. Also
    returns [] for an attachment referencing an existing SharePoint/OneDrive
    file (no usable download link on the activity for that case — see this
    module's docstring) rather than attempting to resolve it.
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
                    "[ATTACHMENT] %r (content_type=%r) has neither downloadUrl nor content_url — skipping "
                    "(likely an existing SharePoint/OneDrive file share, which this bot doesn't fetch)",
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

    if raw_attachments:
        logger.info("[ATTACHMENT] Downloaded %d of %d attachment(s)", len(results), len(raw_attachments))
    return results
