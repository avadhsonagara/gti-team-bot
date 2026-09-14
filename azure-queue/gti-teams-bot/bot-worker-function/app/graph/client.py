"""
Microsoft Graph API client — generic app-only HTTP access to Microsoft Graph.

Reuses the same User-Assigned Managed Identity as the bot's Bot Framework
auth (app/teams/bot_client.py, MANAGED_IDENTITY_CLIENT_ID) — no client
secret, ever, matching this production bot's Managed-Identity-only model.

That identity must be granted the Graph APPLICATION permission
`ChannelMessage.Read.All` with tenant-admin consent — separate from the Bot
Framework permissions already in use.

Plain synchronous `requests`/`azure-identity` calls throughout — no
microsoft-teams-apps SDK, no async.
"""
import logging
import threading
import time

import requests
from azure.identity import ManagedIdentityCredential

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60


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
        self._session: requests.Session | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()

    def _get_session(self) -> requests.Session:
        if self._session is None:
            with self._lock:
                if self._session is None:
                    self._session = requests.Session()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _fetch_token_via_managed_identity(self) -> tuple[str, float]:
        """Returns (token, seconds_until_expiry)."""
        credential = ManagedIdentityCredential(client_id=self.managed_identity_client_id)
        result = credential.get_token(_GRAPH_SCOPE)
        seconds_remaining = max(0.0, result.expires_on - time.time())
        return result.token, seconds_remaining

    def _get_token(self) -> str:
        """Return a cached app-only Graph token, refreshing it if near expiry."""
        if self._token and time.monotonic() < self._token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS:
            return self._token

        # graph_client is a module-level singleton shared across whatever
        # concurrent requests an Azure Functions instance's thread pool is
        # running — without this lock, two overlapping requests can both see
        # an expired token above and both refresh it concurrently.
        with self._lock:
            if self._token and time.monotonic() < self._token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS:
                return self._token

            if not self.managed_identity_client_id:
                raise GraphError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

            token, seconds_remaining = self._fetch_token_via_managed_identity()
            self._token = token
            self._token_expires_at = time.monotonic() + seconds_remaining
            return self._token


# Shared client instance
graph_client = GraphClient()
