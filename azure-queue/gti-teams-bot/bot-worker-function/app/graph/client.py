"""
Microsoft Graph API client for app-only HTTP access.

Acquires bearer tokens via User-Assigned Managed Identity and provides authenticated
HTTP requests to Microsoft Graph endpoints for channel messages and attachments.
"""
import logging
import threading
import time

import requests
from azure.identity import ManagedIdentityCredential
from requests.adapters import HTTPAdapter

from app.config import settings
from app.constants import GRAPH_API_TIMEOUT_SECONDS, TOKEN_EXPIRY_SAFETY_SECONDS

logger = logging.getLogger("gti-teams-bot")

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphError(Exception):
    """Raised when a Microsoft Graph request fails."""


class GraphClient:
    """Client for app-only Microsoft Graph calls using Managed Identity authentication."""

    def __init__(
        self,
        managed_identity_client_id: str | None = None,
        timeout: float = GRAPH_API_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initialize the Microsoft Graph client.

        Args:
            managed_identity_client_id: Optional client ID of the User-Assigned Managed Identity.
            timeout: Default HTTP request timeout in seconds.
        """
        self.managed_identity_client_id = managed_identity_client_id or settings.managed_identity_client_id
        self.timeout = timeout
        self._session: requests.Session | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()

    def _get_session(self) -> requests.Session:
        """
        Retrieve or lazily initialize the thread-safe HTTP session.

        Returns:
            Configured requests.Session instance.
        """
        if self._session is None:
            with self._lock:
                if self._session is None:
                    session = requests.Session()
                    adapter = HTTPAdapter(
                        pool_connections=settings.concurrent_requests,
                        pool_maxsize=settings.concurrent_requests,
                    )
                    session.mount("https://", adapter)
                    self._session = session
        return self._session

    def close(self) -> None:
        """Close the underlying HTTP session and release connection resources."""
        if self._session is not None:
            self._session.close()
            self._session = None

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _fetch_token_via_managed_identity(self) -> tuple[str, float]:
        """
        Acquire a Microsoft Graph access token using Managed Identity.

        Returns:
            Tuple of (token_string, seconds_until_expiry).
        """
        credential = ManagedIdentityCredential(client_id=self.managed_identity_client_id)
        result = credential.get_token(_GRAPH_SCOPE)
        seconds_remaining = max(0.0, result.expires_on - time.time())
        return result.token, seconds_remaining

    def _get_token(self) -> str:
        """
        Retrieve a valid cached Graph token, refreshing it if nearing expiration.

        Returns:
            Bearer token string.

        Raises:
            GraphError: If MANAGED_IDENTITY_CLIENT_ID is not configured.
        """
        if self._token and time.monotonic() < self._token_expires_at - TOKEN_EXPIRY_SAFETY_SECONDS:
            return self._token

        with self._lock:
            if self._token and time.monotonic() < self._token_expires_at - TOKEN_EXPIRY_SAFETY_SECONDS:
                return self._token

            if not self.managed_identity_client_id:
                raise GraphError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

            token, seconds_remaining = self._fetch_token_via_managed_identity()
            self._token = token
            self._token_expires_at = time.monotonic() + seconds_remaining
            return self._token

    # ── Public API ───────────────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> requests.Response:
        """
        Execute an authenticated GET request against Microsoft Graph.

        Args:
            url: Full Microsoft Graph endpoint URL.
            **kwargs: Additional arguments passed to requests.Session.get.

        Returns:
            Response object from the Graph API.
        """
        kwargs.setdefault("timeout", self.timeout)
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Authorization", f"Bearer {self._get_token()}")
        return self._get_session().get(url, headers=headers, **kwargs)


# Shared client instance
graph_client = GraphClient()
