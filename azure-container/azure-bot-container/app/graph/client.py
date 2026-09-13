"""
Microsoft Graph API client — generic app-only HTTP access to Microsoft Graph.

Reuses the same User-Assigned Managed Identity as the bot's Bot Framework
auth (app/teams/bot_client.py, MANAGED_IDENTITY_CLIENT_ID) — no client
secret, ever, matching this production bot's Managed-Identity-only model.

That identity must be granted the Graph APPLICATION permission
`ChannelMessage.Read.All` with tenant-admin consent — separate from the Bot
Framework permissions already in use.

Fully async — httpx.AsyncClient, azure.identity.aio.
"""
import asyncio
import logging

import httpx
from azure.identity.aio import ManagedIdentityCredential

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphError(Exception):
    """Raised when a Microsoft Graph request fails."""


class GraphClient:
    """Client for app-only Microsoft Graph calls."""

    def __init__(
        self,
        managed_identity_client_id: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.managed_identity_client_id = managed_identity_client_id or settings.managed_identity_client_id
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # ManagedIdentityCredential caches tokens internally and is safe to
        # reuse for the process lifetime — no need to duplicate that caching
        # (or its expiry bookkeeping) here; a lock still guards lazy creation
        # of the two lazily-built objects below.
        self._credential: ManagedIdentityCredential | None = None
        self._lock = asyncio.Lock()

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    # ── Auth ─────────────────────────────────────────────────────────────────

    async def _get_credential(self) -> ManagedIdentityCredential:
        if self._credential is None:
            async with self._lock:
                if self._credential is None:
                    if not self.managed_identity_client_id:
                        raise GraphError("MANAGED_IDENTITY_CLIENT_ID is not configured.")
                    self._credential = ManagedIdentityCredential(client_id=self.managed_identity_client_id)
        return self._credential

    async def get_token(self) -> str:
        """Return an app-only Graph token — cached and refreshed internally by ManagedIdentityCredential."""
        credential = await self._get_credential()
        result = await credential.get_token(_GRAPH_SCOPE)
        return result.token


# Shared client instance
graph_client = GraphClient()
