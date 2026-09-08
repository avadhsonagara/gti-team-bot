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
    Framework Connector endpoint that requires the bot's own token.
    Reusing `ctx.api.http` (the SDK's own authenticated client) attaches
    that token automatically instead of us handling it manually.

Attachments Teams adds for its own message rendering (HTML previews,
Adaptive/Hero/Thumbnail cards) are filtered out — they aren't user files.

Only called for channel and group-chat scopes — see handle_message() in
handlers.py for why personal (1:1) messages don't go through this path.
"""
import logging

import httpx

logger = logging.getLogger("gti-teams-bot")

_FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"
_NON_FILE_PREFIXES = ("text/html", "application/vnd.microsoft.card.")

_DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _is_user_file(content_type: str) -> bool:
    if not content_type:
        return False
    return not content_type.startswith(_NON_FILE_PREFIXES)


async def download_attachments(ctx) -> list[tuple[str, bytes, str]]:
    """
    Best-effort download of every user-shared file/image attachment on this
    message activity, as [(filename, bytes, content_type), ...].

    Returns [] when there are none, or on total failure — losing an
    attachment is far less harmful than failing the whole query over it.
    """
    attachments = getattr(ctx.activity, "attachments", None) or []
    results: list[tuple[str, bytes, str]] = []

    async with httpx.AsyncClient(follow_redirects=True) as anon_client:
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
                    resp = await anon_client.get(download_url, timeout=_DOWNLOAD_TIMEOUT)
                    resp.raise_for_status()
                    data = resp.content
                    mime = "application/octet-stream"
                elif attachment.content_url:
                    resp = await ctx.api.http.get(attachment.content_url, timeout=_DOWNLOAD_TIMEOUT)
                    data = resp.content
                    mime = content_type or "application/octet-stream"
                else:
                    continue

                logger.info("[ATTACHMENT] Downloaded %r (%d bytes, %s)", name, len(data), mime)
                results.append((name, data, mime))

            except Exception:
                logger.exception("[ATTACHMENT] Failed to download %r (content_type=%s)", name, content_type)

    return results
