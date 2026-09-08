"""
Downloading file/image attachments a user shared with the bot in a Teams
message, so they can be forwarded to the GTI Agentic API as multipart
artifacts (see gti.client.GTIAgenticClient.send_message(..., files=...)).

Two distinct download paths per the Bot Framework attachment schema:
  - "application/vnd.microsoft.teams.file.download.info": a direct file
    upload. The bytes live behind `content["downloadUrl"]`, a pre-signed
    URL that needs no bot credentials — and must NOT be sent any, since
    it's a foreign host outside the Bot Framework Connector.
  - Anything else with a `content_url` (e.g. inline/pasted images,
    `image/*`): the bytes live behind `content_url`, a protected Bot
    Framework Connector endpoint that requires the bot's own token. The
    token is resolved the same way the SDK resolves it for every other
    Connector call (`ctx.api.http`'s own token source) and attached here
    manually as an Authorization header.

Uses `requests`, same as this app's other HTTP clients (gti/client.py,
graph/client.py). requests is synchronous, so each download runs via
asyncio.to_thread() to avoid blocking the single shared event loop — see
bot.py's _patch_token_validator_for_async_jwks() for why a blocking call
anywhere in a message handler is a real problem here, not a theoretical one.

Attachments Teams adds for its own message rendering (HTML previews,
Adaptive/Hero/Thumbnail cards) are filtered out — they aren't user files.

Only called for channel and group-chat scopes — see handle_message() in
handlers.py for why personal (1:1) messages don't go through this path.
"""
import asyncio
import logging
from typing import Optional

import requests

logger = logging.getLogger("gti-teams-bot")

_FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"
_NON_FILE_PREFIXES = ("text/html", "application/vnd.microsoft.card.")

_DOWNLOAD_TIMEOUT = (10.0, 30.0)  # (connect, read) seconds


def _is_user_file(content_type: str) -> bool:
    if not content_type:
        return False
    return not content_type.startswith(_NON_FILE_PREFIXES)


def _download(url: str, headers: Optional[dict[str, str]] = None) -> bytes:
    """Blocking GET — always run via asyncio.to_thread(), never awaited directly."""
    resp = requests.get(url, headers=headers, timeout=_DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    return resp.content


async def download_attachments(ctx) -> list[tuple[str, bytes, str]]:
    """
    Best-effort download of every user-shared file/image attachment on this
    message activity, as [(filename, bytes, content_type), ...].

    Returns [] when there are none, or on total failure — losing an
    attachment is far less harmful than failing the whole query over it.
    """
    attachments = getattr(ctx.activity, "attachments", None) or []
    results: list[tuple[str, bytes, str]] = []

    for attachment in attachments:
        content_type = attachment.content_type or ""
        if not _is_user_file(content_type):
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

    return results
