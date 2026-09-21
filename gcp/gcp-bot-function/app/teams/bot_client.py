"""
Outbound Bot Framework Connector API access: the bot's own token, and
sending, updating, and deleting a Teams message (update/delete are used to
edit or remove the bot's own "looking into that…" placeholder).
"""
import logging
import threading
import time

import requests

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60

_session = requests.Session()
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
