"""
Microsoft Graph API client — app-only (client-credentials) access to
Microsoft Graph.

Requires the bot's Entra app registration (CLIENT_ID/CLIENT_SECRET/TENANT_ID)
to be granted the Graph application permission the caller needs (e.g.
`ChannelMessage.Read.All` for channel thread history — see app/teams/thread.py),
with tenant-admin consent.
"""
import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter

from app.config import settings

logger = logging.getLogger("gti-teams-bot")

_TOKEN_URL_TMPL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_TOKEN_EXPIRY_SAFETY_SECONDS = 60


class GraphError(Exception):
    """Raised when a Microsoft Graph request fails."""


class GraphClient:
    """Client for app-only Microsoft Graph calls (client credentials flow)."""

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
        self._session_lock = threading.Lock()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    def _get_session(self) -> requests.Session:
        """
        Return or lazily initialize the shared requests.Session. Locked
        (double-checked), same reasoning as _get_token() below — without
        this, two concurrent requests racing a cold cache each build and
        mount their own session/connection pool, and the loser's is silently
        discarded.
        """
        if self._session is None:
            with self._session_lock:
                if self._session is None:
                    session = requests.Session()
                    # Sized to settings.concurrent_requests (see
                    # app/config.py) rather than urllib3's default of 10.
                    adapter = HTTPAdapter(
                        pool_connections=settings.concurrent_requests,
                        pool_maxsize=settings.concurrent_requests,
                    )
                    session.mount("https://", adapter)
                    self._session = session
        return self._session

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _token_is_fresh(self) -> bool:
        return bool(self._token) and time.monotonic() < self._token_expires_at - _TOKEN_EXPIRY_SAFETY_SECONDS

    def _get_token(self) -> str:
        """
        Return a cached app-only Graph token, refreshing it if near expiry.
        Locked (double-checked) so concurrent requests on a cache miss don't
        each independently hit the token endpoint.
        """
        if self._token_is_fresh():
            return self._token

        with self._token_lock:
            if self._token_is_fresh():
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
            response = session.post(url, data=data, timeout=self.timeout)
            if response.status_code != 200:
                raise GraphError(f"Graph token request failed ({response.status_code}): {response.text}")

            payload = response.json()
            self._token = payload["access_token"]
            self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
            return self._token


# Shared client instance
graph_client = GraphClient()
