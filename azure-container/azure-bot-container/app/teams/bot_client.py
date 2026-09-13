"""
Outbound Bot Framework Connector API access — the bot's own token, and
sending/updating/deleting a Teams message. Fully async — httpx.AsyncClient,
azure.identity.aio.

Token acquisition is Managed-Identity-only: this is a production bot, and
the User-Assigned Managed Identity (MANAGED_IDENTITY_CLIENT_ID) is always
available since it's provisioned together with this app's compute resource —
no client secret ever enters this codebase. Extended here with update/delete
since this bot (unlike rs-alerts) edits and removes its own "looking into
that…" placeholder.
"""
import asyncio
import logging

import httpx
from azure.identity.aio import ManagedIdentityCredential

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

_client = httpx.AsyncClient(timeout=30.0)
# ManagedIdentityCredential caches tokens internally and is safe to reuse for
# the process lifetime — no need for our own expiry bookkeeping on top of it;
# the lock just guards this one lazy construction, not each get_token() call.
_credential: ManagedIdentityCredential | None = None
_credential_lock = asyncio.Lock()


async def _get_credential() -> ManagedIdentityCredential:
    global _credential
    if _credential is None:
        async with _credential_lock:
            if _credential is None:
                if not settings.managed_identity_client_id:
                    raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")
                _credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    return _credential


async def get_bot_token() -> str:
    """Return an app-only Bot Framework Connector token — cached and refreshed internally by ManagedIdentityCredential."""
    credential = await _get_credential()
    result = await credential.get_token(_BOTFRAMEWORK_SCOPE)
    return result.token


async def _headers() -> dict:
    return {"Authorization": f"Bearer {await get_bot_token()}", "Content-Type": "application/json"}


async def close() -> None:
    """Close the underlying HTTP client and credential (call on app shutdown)."""
    global _credential
    await _client.aclose()
    if _credential is not None:
        await _credential.close()
        _credential = None


async def send_activity(service_url: str, conversation_id: str, activity: dict) -> dict:
    """POST a new activity to a conversation. Returns the Connector API response (includes 'id')."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    resp = await _client.post(url, headers=await _headers(), json=activity)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


async def update_activity(service_url: str, conversation_id: str, activity_id: str, activity: dict) -> dict:
    """PUT (edit in place) an existing activity."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = await _client.put(url, headers=await _headers(), json=activity)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


async def delete_activity(service_url: str, conversation_id: str, activity_id: str) -> None:
    """DELETE an existing activity."""
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    resp = await _client.delete(url, headers=await _headers())
    resp.raise_for_status()
