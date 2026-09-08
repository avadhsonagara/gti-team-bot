"""
Microsoft Graph API client — generic app-only (client-credentials) HTTP
access to Microsoft Graph.

Requires the bot's Entra app registration (CLIENT_ID/CLIENT_SECRET/TENANT_ID)
to be granted whatever Graph application permission the caller needs (e.g.
`ChannelMessage.Read.All` for channel thread history — see app/teams/thread.py)
with tenant-admin consent. This is separate from — and in addition to — the
Bot Framework permissions the app already uses to send/receive messages.

Uses `requests` (synchronous) rather than an async HTTP client — every call
is offloaded to a thread via asyncio.to_thread() so it can't block the
single shared event loop this app runs on.
"""
import asyncio
import logging
import time

import requests

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_TOKEN_URL_TMPL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60


class GraphError(Exception):
    """Raised when a Microsoft Graph request fails."""


class GraphClient:
    """Async-facing client for app-only Microsoft Graph calls (client credentials flow)."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.client_id = client_id or settings.client_id
        self.client_secret = client_secret or settings.client_secret
        self.tenant_id = tenant_id or settings.tenant_id
        self.timeout = timeout
        self._session: requests.Session | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await asyncio.to_thread(self._session.close)
            self._session = None

    # ── Auth ─────────────────────────────────────────────────────────────────

    async def _get_token(self) -> str:
        """Return a cached app-only Graph token, refreshing it if near expiry."""
        if self._token and time.monotonic() < self._token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS:
            return self._token

        if not (self.client_id and self.client_secret and self.tenant_id):
            raise GraphError("CLIENT_ID/CLIENT_SECRET/TENANT_ID are required for Graph auth.")

        session = self._get_session()
        url = _TOKEN_URL_TMPL.format(tenant_id=self.tenant_id)
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        response = await asyncio.to_thread(session.post, url, data=data, timeout=self.timeout)
        if response.status_code != 200:
            raise GraphError(f"Graph token request failed ({response.status_code}): {response.text}")

        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
        return self._token


# Shared client instance
graph_client = GraphClient()
