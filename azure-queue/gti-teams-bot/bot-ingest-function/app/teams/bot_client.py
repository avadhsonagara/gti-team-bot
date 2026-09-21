"""
Client for outbound Bot Framework Connector API requests.

Handles token acquisition via Azure Managed Identity, and provides functions
to send, update, and delete messages in Microsoft Teams conversations.
"""
import logging
import threading
import time

import requests
from azure.identity import ManagedIdentityCredential
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from app.config import settings
from app.constants import (
    BOT_CONNECTOR_RETRY_BACKOFF_FACTOR,
    BOT_CONNECTOR_RETRY_STATUS_FORCELIST,
    BOT_CONNECTOR_RETRY_TOTAL,
    BOT_CONNECTOR_TIMEOUT,
    TOKEN_EXPIRY_SAFETY_SECONDS,
)

logger = logging.getLogger("gti-teams-bot")

_BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

_session = requests.Session()
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


def _fetch_token_via_managed_identity() -> tuple[str, float]:
    """
    Acquire an app-only Bot Framework Connector token using Azure Managed Identity.

    Returns:
        Tuple of (access_token, seconds_until_expiry).
    """
    credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    result = credential.get_token(_BOTFRAMEWORK_SCOPE)
    seconds_remaining = max(0.0, result.expires_on - time.time())
    return result.token, seconds_remaining


def get_bot_token() -> str:
    """
    Return a cached Bot Framework Connector token, refreshing if near expiry.

    Returns:
        Bearer access token string.
    """
    global _bot_token, _bot_token_expires_at
    if _bot_token and time.monotonic() < _bot_token_expires_at - TOKEN_EXPIRY_SAFETY_SECONDS:
        return _bot_token

    with _token_lock:
        if _bot_token and time.monotonic() < _bot_token_expires_at - TOKEN_EXPIRY_SAFETY_SECONDS:
            return _bot_token

        if not settings.managed_identity_client_id:
            raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

        token, seconds_remaining = _fetch_token_via_managed_identity()
        _bot_token = token
        _bot_token_expires_at = time.monotonic() + seconds_remaining
        return _bot_token


def _headers() -> dict:
    """Return default HTTP headers including authorization and JSON content-type."""
    return {"Authorization": f"Bearer {get_bot_token()}", "Content-Type": "application/json"}


def send_activity(service_url: str, conversation_id: str, activity: dict) -> dict:
    """
    Post a new activity to a Teams conversation.

    Args:
        service_url: Base URL for the Bot Framework Connector service.
        conversation_id: Target conversation ID.
        activity: Activity payload dictionary.

    Returns:
        Response dictionary from the Bot Framework Connector containing activity ID.
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
    """
    Update an existing activity in a Teams conversation.

    Args:
        service_url: Base URL for the Bot Framework Connector service.
        conversation_id: Target conversation ID.
        activity_id: ID of the activity to update.
        activity: Updated activity payload dictionary.

    Returns:
        Response dictionary from the Bot Framework Connector.
    """
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = _session.put(url, headers=_headers(), json=activity, timeout=BOT_CONNECTOR_TIMEOUT)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def delete_activity(service_url: str, conversation_id: str, activity_id: str) -> None:
    """
    Delete an existing activity from a Teams conversation.

    Args:
        service_url: Base URL for the Bot Framework Connector service.
        conversation_id: Target conversation ID.
        activity_id: ID of the activity to delete.
    """
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = _session.delete(url, headers=_headers(), timeout=BOT_CONNECTOR_TIMEOUT)
    resp.raise_for_status()
