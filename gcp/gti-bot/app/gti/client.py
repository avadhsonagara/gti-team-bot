"""
Google Threat Intelligence (GTI) Agentic API Client.

Directly connects to the VirusTotal / GTI Agentic Sessions API:
  - Create new session: POST /agentspace/sessions
  - Post message to session: POST /agentspace/sessions/{session_id}
  - Get session details: GET /agentspace/sessions/{session_id}
  - Delete session: DELETE /agentspace/sessions/{session_id}
"""
import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("gti-teams-bot")


# ── Custom Exceptions ─────────────────────────────────────────────────────────

class GTIError(Exception):
    """Base exception for all GTI Agentic API errors."""


class GTIAuthenticationError(GTIError):
    """Raised when the GTI API key is invalid or unauthorized (HTTP 401 / 403)."""


class GTISessionNotFoundError(GTIError):
    """Raised when the requested session ID is not found or has expired (HTTP 404)."""


class GTIRateLimitError(GTIError):
    """Raised when API quota is exhausted or rate limit is hit (HTTP 429)."""


class GTIServiceError(GTIError):
    """Raised when the GTI service returns a 5xx error or is temporarily unavailable."""


class GTIBadRequestError(GTIError):
    """Raised for a permanent 4xx client error (e.g. malformed request) — not retried."""


class GTITimeoutError(GTIError):
    """Raised when the request to the GTI Agentic API times out."""


# ── GTI Agentic API Client ───────────────────────────────────────────────────

class GTIAgenticClient:
    """Async client for the Google Threat Intelligence Agentic API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_delay: float | None = None,
    ) -> None:
        self.api_key = api_key or settings.gti_api_key
        self.base_url = (base_url or settings.gti_api_base_url).rstrip("/")
        self.timeout = timeout if timeout is not None else 240.0
        self.max_retries = max_retries if max_retries is not None else 2
        self.retry_delay = retry_delay if retry_delay is not None else 2.0
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return or lazily initialize the shared httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                headers={
                    "x-apikey": self.api_key,
                    "User-Agent": "gti-teams-bot-agentic-gcp/1.0",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Response Text Extraction ──────────────────────────────────────────────

    def _extract_response_text(self, data: dict[str, Any]) -> str:
        """
        Extract the latest AGENT_FINAL_RESPONSE markdown text from session events.
        """
        if not isinstance(data, dict):
            return "No response generated."

        events = (
            (data.get("data") or {})
            .get("attributes", {})
            .get("events", [])
        )
        if not events:
            return "No response events returned by GTI Agentic API."

        # Scan events in reverse to find the latest AGENT_FINAL_RESPONSE
        for event in reversed(events):
            if event.get("message_type") == "AGENT_FINAL_RESPONSE":
                final_resp = event.get("agent_final_response", {})
                widgets = final_resp.get("widgets", [])
                text_parts: list[str] = []
                for widget in widgets:
                    if widget.get("widget_type") == "MARKDOWN_TEXT":
                        md_text = widget.get("markdown_text_widget", {}).get("text")
                        if md_text:
                            text_parts.append(md_text.strip())
                if text_parts:
                    return "\n\n".join(text_parts)

        # Fallback: if no AGENT_FINAL_RESPONSE, check for agent thoughts or generic widget
        for event in reversed(events):
            thought = event.get("agent_thought", {})
            widgets = thought.get("widgets", [])
            for widget in widgets:
                if widget.get("widget_type") == "MARKDOWN_TEXT":
                    md_text = widget.get("markdown_text_widget", {}).get("text")
                    if md_text:
                        return md_text.strip()

        return "Analysis completed, but no displayable text was generated."

    # ── Core Request Runner with Retries ───────────────────────────────────────

    async def _send_request_with_retries(
        self,
        method: str,
        endpoint: str,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute an HTTP request against the GTI Agentic API with exponential backoff.
        """
        if not self.api_key:
            raise GTIAuthenticationError(
                "GTI_API_KEY is missing. Please configure your API key in Secret Manager, .env, or environment."
            )

        client = await self._get_client()
        url = endpoint

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(
                    "[GTI] %s %s (attempt %d/%d)",
                    method, endpoint, attempt + 1, self.max_retries + 1,
                )
                response = await client.request(
                    method=method,
                    url=url,
                    files=files,
                    data=data,
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code in (401, 403):
                    logger.error("[GTI] Authentication error (%d): %s", response.status_code, response.text)
                    raise GTIAuthenticationError(f"GTI API Authentication failed ({response.status_code}).")

                if response.status_code == 404:
                    logger.warning("[GTI] Session not found (%d): %s", response.status_code, response.text)
                    raise GTISessionNotFoundError(f"Session not found or expired ({response.status_code}).")

                if response.status_code == 429:
                    logger.error("[GTI] Rate limit / quota exceeded (%d): %s", response.status_code, response.text)
                    raise GTIRateLimitError("GTI API rate limit or quota exceeded.")

                if response.status_code in (500, 502, 503, 504):
                    logger.warning("[GTI] Transient server error (%d): %s", response.status_code, response.text)
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (2 ** attempt)
                        logger.info("[GTI] Retrying in %.1fs...", delay)
                        await asyncio.sleep(delay)
                        continue
                    raise GTIServiceError(f"GTI service error ({response.status_code}): {response.text}")

                if 400 <= response.status_code < 500:
                    logger.error("[GTI] Bad request (%d): %s", response.status_code, response.text)
                    raise GTIBadRequestError(f"GTI API rejected the request ({response.status_code}).")

                # Other HTTP errors
                response.raise_for_status()
                return response.json()

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                logger.warning("[GTI] Connection/timeout error: %s", exc)
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.info("[GTI] Retrying network error in %.1fs...", delay)
                    await asyncio.sleep(delay)
                    continue
                raise GTITimeoutError(f"GTI request timed out or network failed: {exc}") from exc

            except (GTIAuthenticationError, GTISessionNotFoundError, GTIRateLimitError, GTIBadRequestError):
                # Don't retry client-side / permanent errors
                raise

            except Exception as exc:
                logger.warning("[GTI] Unexpected error: %s", exc)
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise GTIServiceError(f"GTI request failed: {exc}") from exc

        if last_exc:
            raise GTIServiceError(f"GTI request failed after retries: {last_exc}") from last_exc
        raise GTIServiceError("GTI request failed after maximum retries.")

    # ── Public API Methods ────────────────────────────────────────────────────

    async def create_session(
        self,
        message: str,
        files: list[tuple[str, bytes, str]] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Create a new agentic session with the initial user message.

        Returns:
            (session_id, response_markdown_text, raw_api_response_dict)
        """
        endpoint = "/agentspace/sessions"
        form_files: dict[str, Any] = {"message": (None, message)}

        if files:
            for idx, (fname, fbytes, ftype) in enumerate(files):
                form_files[f"files[{idx}]"] = (fname, fbytes, ftype)

        raw_data = await self._send_request_with_retries(
            method="POST",
            endpoint=endpoint,
            files=form_files,
        )

        session_id = (raw_data.get("data") or {}).get("id", "")
        text = self._extract_response_text(raw_data)
        logger.info("[GTI] Created new session id=%s | text_len=%d", session_id, len(text))
        return session_id, text, raw_data

    async def post_session_message(
        self,
        session_id: str,
        message: str,
        files: list[tuple[str, bytes, str]] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Post a follow-up message to an existing agentic session.

        Returns:
            (session_id, response_markdown_text, raw_api_response_dict)
        """
        endpoint = f"/agentspace/sessions/{session_id}"
        form_files: dict[str, Any] = {"message": (None, message)}

        if files:
            for idx, (fname, fbytes, ftype) in enumerate(files):
                form_files[f"files[{idx}]"] = (fname, fbytes, ftype)

        raw_data = await self._send_request_with_retries(
            method="POST",
            endpoint=endpoint,
            files=form_files,
        )

        text = self._extract_response_text(raw_data)
        logger.info("[GTI] Continued session id=%s | text_len=%d", session_id, len(text))
        return session_id, text, raw_data

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """
        Retrieve details and event history for an existing session.
        """
        endpoint = f"/agentspace/sessions/{session_id}"
        return await self._send_request_with_retries(
            method="GET",
            endpoint=endpoint,
        )

    async def delete_session(self, session_id: str) -> bool:
        """
        Delete an existing agentic session.
        """
        endpoint = f"/agentspace/sessions/{session_id}"
        try:
            await self._send_request_with_retries(
                method="DELETE",
                endpoint=endpoint,
            )
            return True
        except Exception as exc:
            logger.warning("[GTI] Failed to delete session %s: %s", session_id, exc)
            return False


# Shared client instance
gti_client = GTIAgenticClient()
