"""
Outbound Bot Framework Connector API access — the bot's own token, and
sending/updating/deleting a Teams message. Plain synchronous `requests`.

Token acquisition matches azure/rs-alerts/app/bot_auth.py's dual-mode
pattern: a User-Assigned Managed Identity in production
(MANAGED_IDENTITY_CLIENT_ID) — no client secret involved — falling back to
an Entra ID client-secret app registration for local dev
(CLIENT_ID/CLIENT_SECRET/TENANT_ID), since managed identity isn't available
outside Azure. Extended here with update/delete since this bot (unlike
rs-alerts) edits and removes its own "looking into that…" placeholder.
"""
import logging
import threading
import time

import requests
from azure.identity import ManagedIdentityCredential

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60

_session = requests.Session()
_bot_token: str | None = None
_bot_token_expires_at: float = 0.0
_token_lock = threading.Lock()


def _fetch_token_via_managed_identity() -> tuple[str, float]:
    """Returns (token, seconds_until_expiry)."""
    credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    result = credential.get_token(_BOTFRAMEWORK_SCOPE)
    seconds_remaining = max(0.0, result.expires_on - time.time())
    return result.token, seconds_remaining


def _fetch_token_via_client_secret() -> tuple[str, float]:
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
    return payload["access_token"], float(payload.get("expires_in", 3600))


def get_bot_token() -> str:
    """Return a cached app-only Bot Framework Connector token, refreshing it if near expiry."""
    global _bot_token, _bot_token_expires_at
    if _bot_token and time.monotonic() < _bot_token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS:
        return _bot_token

    # Azure Functions can run a request pool with more than one worker thread
    # per instance — without this lock, two overlapping requests can both see
    # an expired token above and both refresh it concurrently.
    with _token_lock:
        if _bot_token and time.monotonic() < _bot_token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS:
            return _bot_token

        if settings.managed_identity_client_id:
            token, seconds_remaining = _fetch_token_via_managed_identity()
        elif settings.client_id and settings.client_secret and settings.tenant_id:
            token, seconds_remaining = _fetch_token_via_client_secret()
        else:
            raise RuntimeError(
                "No Bot Framework credentials configured: set either MANAGED_IDENTITY_CLIENT_ID "
                "or CLIENT_ID + CLIENT_SECRET + TENANT_ID."
            )

        _bot_token = token
        _bot_token_expires_at = time.monotonic() + seconds_remaining
        return _bot_token


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_bot_token()}", "Content-Type": "application/json"}


def send_activity(service_url: str, conversation_id: str, activity: dict) -> dict:
    """POST a new activity to a conversation. Returns the Connector API response (includes 'id')."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    resp = _session.post(url, headers=_headers(), json=activity, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def update_activity(service_url: str, conversation_id: str, activity_id: str, activity: dict) -> dict:
    """PUT (edit in place) an existing activity."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = _session.put(url, headers=_headers(), json=activity, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def delete_activity(service_url: str, conversation_id: str, activity_id: str) -> None:
    """DELETE an existing activity."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = _session.delete(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
