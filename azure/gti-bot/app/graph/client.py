"""
Microsoft Graph API client — generic app-only HTTP access to Microsoft Graph.

Reuses the same Azure AD identity as the Teams SDK's Bot Framework auth: a
User-Assigned Managed Identity in production (MANAGED_IDENTITY_CLIENT_ID),
falling back to a client-secret app registration for local dev
(CLIENT_ID/CLIENT_SECRET/TENANT_ID) — the same dual-mode pattern as
azure/rs-alerts/app/graph_client.py (that module is sync throughout; this
one backs an async app, so token acquisition and HTTP calls are async here).

Either identity must be granted the Graph APPLICATION permission
`ChannelMessage.Read.All` with tenant-admin consent — separate from the Bot
Framework permissions already in use.
"""
import asyncio
import logging
import time

import httpx
from azure.identity import ManagedIdentityCredential

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_TOKEN_URL_TMPL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60


class GraphError(Exception):
    """Raised when a Microsoft Graph request fails."""


class GraphClient:
    """Async client for app-only Microsoft Graph calls."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        managed_identity_client_id: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.client_id = client_id or settings.client_id
        self.client_secret = client_secret or settings.client_secret
        self.tenant_id = tenant_id or settings.tenant_id
        self.managed_identity_client_id = managed_identity_client_id or settings.managed_identity_client_id
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _fetch_token_via_managed_identity(self) -> tuple[str, float]:
        """
        Blocking (azure-identity's sync ManagedIdentityCredential does a real
        network call) — only ever call this through asyncio.to_thread(), never
        directly on the event loop this whole app shares for every request
        (same reasoning as the JWKS-fetch patch in app/teams/bot.py).

        Returns (token, seconds_until_expiry) — expires_on is Unix-epoch
        seconds, converted here to a duration so the caller can track it
        against time.monotonic() consistently with the client-secret path.
        """
        credential = ManagedIdentityCredential(client_id=self.managed_identity_client_id)
        result = credential.get_token(_GRAPH_SCOPE)
        seconds_remaining = max(0.0, result.expires_on - time.time())
        return result.token, seconds_remaining

    async def _get_token(self) -> str:
        """Return a cached app-only Graph token, refreshing it if near expiry."""
        if self._token and time.monotonic() < self._token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS:
            return self._token

        if self.managed_identity_client_id:
            token, seconds_remaining = await asyncio.to_thread(self._fetch_token_via_managed_identity)
            self._token = token
            self._token_expires_at = time.monotonic() + seconds_remaining
            return self._token

        if not (self.client_id and self.client_secret and self.tenant_id):
            raise GraphError(
                "No Azure AD credentials configured for Microsoft Graph: set either "
                "MANAGED_IDENTITY_CLIENT_ID or CLIENT_ID + CLIENT_SECRET + TENANT_ID."
            )

        client = await self._get_client()
        url = _TOKEN_URL_TMPL.format(tenant_id=self.tenant_id)
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": _GRAPH_SCOPE,
        }
        response = await client.post(url, data=data)
        if response.status_code != 200:
            raise GraphError(f"Graph token request failed ({response.status_code}): {response.text}")

        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
        return self._token


# Shared client instance
graph_client = GraphClient()
