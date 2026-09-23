"""
Outbound Bot Framework Connector API access: the bot's own token, and
sending, updating, and deleting a Teams message (update/delete are used to
edit or remove the bot's own "looking into that…" placeholder).
"""
import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from app.config import settings
from app.constants import (
    BOT_CONNECTOR_RETRY_BACKOFF_FACTOR,
    BOT_CONNECTOR_RETRY_STATUS_FORCELIST,
    BOT_CONNECTOR_RETRY_TOTAL,
    BOT_CONNECTOR_TIMEOUT,
)

logger = logging.getLogger("gti-teams-bot")

_BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60

_session = requests.Session()
# Retry.DEFAULT_ALLOWED_METHODS (the default here, left unset deliberately)
# excludes POST — send_activity() below is the only POST caller, and it
# creates a brand-new message, so retrying it on an ambiguous failure (e.g.
# a 503 where the request may have already been processed) risks double-
# posting to the user. update_activity()/delete_activity() (PUT/DELETE) are
# idempotent and safe to retry, and are covered by the default method set.
_retry_strategy = Retry(
    total=BOT_CONNECTOR_RETRY_TOTAL,
    backoff_factor=BOT_CONNECTOR_RETRY_BACKOFF_FACTOR,
    status_forcelist=list(BOT_CONNECTOR_RETRY_STATUS_FORCELIST),
    raise_on_status=False,
)
_session.mount(
    "https://",
    HTTPAdapter(
        max_retries=_retry_strategy,
        pool_connections=settings.concurrent_requests,
        pool_maxsize=settings.concurrent_requests,
    ),
)
_bot_token: str | None = None
_bot_token_expires_at: float = 0.0
_token_lock = threading.Lock()


def _token_is_fresh() -> bool:
    return bool(_bot_token) and time.monotonic() < _bot_token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS


def get_bot_token() -> str:
    """
    Return a cached app-only Bot Framework Connector token, refreshing it if
    near expiry. Locked (double-checked) so concurrent requests on a cache
    miss don't each independently hit the token endpoint — see gcp-bot-
    function/docs concurrency review, Finding C.
    """
    global _bot_token, _bot_token_expires_at
    if _token_is_fresh():
        return _bot_token

    with _token_lock:
        if _token_is_fresh():
            return _bot_token

        if not (settings.client_id and settings.client_secret and settings.tenant_id):
            raise RuntimeError("Missing Bot Framework credentials: set CLIENT_ID + CLIENT_SECRET + TENANT_ID.")

        url = f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "scope": _BOTFRAMEWORK_SCOPE,
            "grant_type": "client_credentials",
        }
        resp = _session.post(url, data=data, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        _bot_token = payload["access_token"]
        _bot_token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
        return _bot_token


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_bot_token()}", "Content-Type": "application/json"}


def send_activity(service_url: str, conversation_id: str, activity: dict) -> dict:
    """
    POST a new activity to a conversation. Returns the Connector API response
    (includes 'id').

    POST is deliberately excluded from the mounted adapter's own retry
    strategy (see _retry_strategy above) to avoid double-posting on an
    ambiguous 5xx failure. A 429 carries no such risk — it means the request
    was throttled before being processed at all — so it's retried here
    instead, respecting Retry-After when the Connector API sends one.
    """
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    for attempt in range(BOT_CONNECTOR_RETRY_TOTAL + 1):
        resp = _session.post(url, headers=_headers(), json=activity, timeout=BOT_CONNECTOR_TIMEOUT)
        if resp.status_code != 429 or attempt == BOT_CONNECTOR_RETRY_TOTAL:
            break
        retry_after = resp.headers.get("Retry-After")
        delay = float(retry_after) if retry_after and retry_after.strip().isdigit() else (
            BOT_CONNECTOR_RETRY_BACKOFF_FACTOR * (2 ** attempt)
        )
        logger.warning(
            "[BOT RETRY] Rate limited (429) sending activity — retrying in %.1fs (attempt %d/%d)...",
            delay, attempt + 1, BOT_CONNECTOR_RETRY_TOTAL,
        )
        time.sleep(delay)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def update_activity(service_url: str, conversation_id: str, activity_id: str, activity: dict) -> dict:
    """PUT (edit in place) an existing activity."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = _session.put(url, headers=_headers(), json=activity, timeout=BOT_CONNECTOR_TIMEOUT)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def delete_activity(service_url: str, conversation_id: str, activity_id: str) -> None:
    """DELETE an existing activity."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = _session.delete(url, headers=_headers(), timeout=BOT_CONNECTOR_TIMEOUT)
    resp.raise_for_status()
