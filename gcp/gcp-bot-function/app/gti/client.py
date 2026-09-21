"""
Google Threat Intelligence (GTI) Agentic API client.

Connects to the VirusTotal / GTI Agentic Sessions API:
  - Create new session: POST /agentspace/sessions
  - Post message to session: POST /agentspace/sessions/{session_id}
"""
import logging
import random
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

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


class GTIClientError(GTIError):
    """Raised for a permanent, non-retryable 4xx error (e.g. malformed request, conflict) other than 401/403/404/413/429."""


class GTIPayloadTooLargeError(GTIClientError):
    """Raised when the request body (typically attached files) exceeds the GTI API's size limit (HTTP 413)."""


class GTITimeoutError(GTIError):
    """Raised when the request to the GTI Agentic API times out."""


class GTIEmptyResponseError(GTIError):
    """Raised when the GTI Agentic API returns a 200 OK response with no displayable agent response text."""


# ── GTI Agentic API Client ───────────────────────────────────────────────────

class GTIAgenticClient:
    """Client for the Google Threat Intelligence Agentic API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_delay: float | None = None,
        rate_limit_retry_delay: float | None = None,
    ) -> None:
        self.api_key = api_key or settings.gti_api_key
        self.base_url = (base_url or settings.gti_api_base_url).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.gti_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else 3
        self.retry_delay = retry_delay if retry_delay is not None else 2.0
        self.rate_limit_retry_delay = rate_limit_retry_delay if rate_limit_retry_delay is not None else 5.0
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        """
        Return or lazily initialize the shared requests.Session.

        Headers are set on a local object before it's ever assigned to
        self._session, so a concurrent caller can never observe a session
        that's missing the x-apikey header
        """
        if self._session is None:
            session = requests.Session()
            session.headers.update({
                "x-apikey": self.api_key,
                "User-Agent": "gti-teams-bot-agentic-gcp/1.0",
            })
            # Sized to settings.concurrent_requests (see app/config.py)
            # rather than urllib3's default of 10, so concurrent Cloud Run
            # requests each get their own pooled connection instead of
            # discarding/recreating one past the default pool size.
            adapter = HTTPAdapter(
                pool_connections=settings.concurrent_requests,
                pool_maxsize=settings.concurrent_requests,
            )
            session.mount("https://", adapter)
            self._session = session
        return self._session

    # ── Response Text Extraction ──────────────────────────────────────────────

    def _extract_response_text(self, data: dict[str, Any]) -> str:
        """
        Extract the latest AGENT_FINAL_RESPONSE markdown text from session events.

        Raises:
            GTIEmptyResponseError: If no valid response text was returned by the agent.
        """
        if not isinstance(data, dict):
            raise GTIEmptyResponseError("GTI API response was not a JSON object.")

        events = (
            (data.get("data") or {})
            .get("attributes", {})
            .get("events", [])
        )
        if not events:
            raise GTIEmptyResponseError("GTI API returned no session events.")

        # Scan events in reverse to find the latest AGENT_FINAL_RESPONSE. The
        # `if text_parts: return ...` must be inside this `if`, not after —
        # otherwise, when the LATEST final-response event has no text (e.g.
        # a chart/table-only widget), the loop would silently keep scanning
        # backwards and return an OLDER turn's text as if it answered the
        # current question, instead of raising.
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
                break

        raise GTIEmptyResponseError("GTI API completed the request but produced no displayable text.")

    # ── Core Request Runner with Retries ───────────────────────────────────────

    def _send_request_with_retries(
        self,
        method: str,
        endpoint: str,
        files: list[tuple[str, Any]] | dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute an HTTP request against the GTI Agentic API with rate limiting
        and exponential backoff for transient failures and 429 rate limits.
        """
        if not self.api_key:
            raise GTIAuthenticationError(
                "GTI_API_KEY is missing. Please configure your API key in Secret Manager, .env, or environment."
            )

        session = self._get_session()
        url = f"{self.base_url}{endpoint}"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if attempt == 0:
                    logger.info("[GTI] %s %s", method, endpoint)
                else:
                    logger.info(
                        "[GTI] %s %s (attempt %d/%d)",
                        method, endpoint, attempt, self.max_retries,
                    )
                response = session.request(
                    method,
                    url,
                    files=files,
                    data=data,
                    timeout=(15.0, self.timeout),
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code in (401, 403):
                    logger.error("[GTI] Authentication error (%d): %s", response.status_code, response.text)
                    raise GTIAuthenticationError(f"GTI API Authentication failed ({response.status_code}).")

                if response.status_code == 404:
                    logger.warning("[GTI] Session not found (%d): %s", response.status_code, response.text)
                    raise GTISessionNotFoundError(f"Session not found or expired ({response.status_code}).")

                if response.status_code == 413:
                    logger.warning("[GTI] Payload too large (413): %s", response.text)
                    raise GTIPayloadTooLargeError(f"Request payload too large ({response.status_code}).")

                if response.status_code == 429:
                    logger.warning("[GTI] Rate limit / quota exceeded (429): %s", response.text)
                    if attempt < self.max_retries:
                        # Check Retry-After header or use 10s, 20s, 30s backoff schedule (+ jitter)
                        retry_after = response.headers.get("Retry-After")
                        if retry_after and retry_after.strip().isdigit():
                            backoff = float(retry_after.strip()) + random.uniform(0.5, 1.5)
                        else:
                            # 10s, 20s, 30s backoff schedule
                            delays = [10.0, 20.0, 30.0]
                            base_delay = delays[min(attempt, len(delays) - 1)]
                            backoff = base_delay + random.uniform(0.5, 2.0)

                        logger.warning(
                            "[GTI] 429 Rate Limit backoff: waiting %.1fs before retry (attempt %d/%d)...",
                            backoff, attempt + 1, self.max_retries,
                        )
                        time.sleep(backoff)
                        continue
                    raise GTIRateLimitError("GTI API rate limit or quota exceeded after retries.")

                if response.status_code in (500, 502, 503, 504):
                    logger.warning("[GTI] Transient server error (%d): %s", response.status_code, response.text)
                    if attempt < self.max_retries:
                        delay = (self.retry_delay * (2 ** attempt)) + random.uniform(0.1, 0.5)
                        logger.info("[GTI] Retrying server error in %.1fs...", delay)
                        time.sleep(delay)
                        continue
                    raise GTIServiceError(f"GTI service error ({response.status_code}): {response.text}")

                if 400 <= response.status_code < 500:
                    logger.error("[GTI] Client error (%d): %s", response.status_code, response.text)
                    raise GTIClientError(f"GTI API rejected the request ({response.status_code}).")

                # Other HTTP errors
                response.raise_for_status()
                return response.json()

            except requests.exceptions.ConnectionError as exc:
                # Fails fast (DNS failure, connection refused, a connect
                # timeout) — cheap to retry, unlike a read timeout below.
                logger.warning("[GTI] Connection error: %s", exc)
                last_exc = exc
                if attempt < self.max_retries:
                    delay = (self.retry_delay * (2 ** attempt)) + random.uniform(0.1, 0.5)
                    logger.info("[GTI] Retrying network error in %.1fs...", delay)
                    time.sleep(delay)
                    continue
                raise GTITimeoutError(f"GTI request failed after retries (connection error): {exc}") from exc

            except requests.exceptions.Timeout as exc:
                # A read timeout means GTI never responded within
                # self.timeout — deliberately NOT retried, unlike the
                # ConnectionError case above. Retrying would re-burn the full
                # self.timeout budget again per attempt, multiplying total
                # user-facing latency for no benefit — a single attempt at
                # the full timeout, then fail fast, keeps latency bounded.
                logger.warning("[GTI] Read timeout after %.0fs: %s", self.timeout, exc)
                raise GTITimeoutError(f"GTI request timed out after {self.timeout:.0f}s: {exc}") from exc

            except (GTIAuthenticationError, GTISessionNotFoundError, GTIClientError, GTIRateLimitError):
                # Don't retry client-side / permanent errors, and don't let
                # GTIRateLimitError fall into the generic `except Exception`
                # below — it would get logged as "[GTI] Unexpected error",
                # re-enter the retry loop, and ultimately be re-raised as
                # GTIServiceError instead, showing the user "Service
                # Unavailable" instead of the correct "Rate Limit Exceeded".
                raise

            except Exception as exc:
                logger.warning("[GTI] Unexpected error: %s", exc)
                last_exc = exc
                if attempt < self.max_retries:
                    delay = (self.retry_delay * (2 ** attempt)) + random.uniform(0.1, 0.5)
                    time.sleep(delay)
                    continue
                raise GTIServiceError(f"GTI request failed: {exc}") from exc

        if last_exc:
            raise GTIServiceError(f"GTI request failed after retries: {last_exc}") from last_exc
        raise GTIServiceError("GTI request failed after maximum retries.")

    # ── Public API Methods ────────────────────────────────────────────────────

    def create_session(
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
        # `files` is a single array-typed field in the API schema, so every file
        # must repeat the SAME field name "files" (a dict can't hold duplicate
        # keys, hence the list-of-tuples form here — requests' documented way
        # to send a repeated multipart field). Using indexed keys like
        # "files[0]"/"files[1]" here previously meant the backend never saw
        # them as part of its "files" array at all.
        form_files: list[tuple[str, Any]] = [("message", (None, message))]

        if files:
            for fname, fbytes, ftype in files:
                form_files.append(("files", (fname, fbytes, ftype)))

        raw_data = self._send_request_with_retries(
            method="POST",
            endpoint=endpoint,
            files=form_files,
        )

        session_id = (raw_data.get("data") or {}).get("id", "")
        text = self._extract_response_text(raw_data)
        logger.info("[GTI] Created new session id=%s | text_len=%d", session_id, len(text))
        return session_id, text, raw_data

    def post_session_message(
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
        # See create_session()'s comment — repeated "files" field name, not indexed keys.
        form_files: list[tuple[str, Any]] = [("message", (None, message))]

        if files:
            for fname, fbytes, ftype in files:
                form_files.append(("files", (fname, fbytes, ftype)))

        raw_data = self._send_request_with_retries(
            method="POST",
            endpoint=endpoint,
            files=form_files,
        )

        text = self._extract_response_text(raw_data)
        logger.info("[GTI] Continued session id=%s | text_len=%d", session_id, len(text))
        return session_id, text, raw_data

    def send_message(
        self,
        message: str,
        session_id: str | None = None,
        files: list[tuple[str, bytes, str]] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Send a message to GTI, continuing `session_id` if given, otherwise
        creating a new session. Falls back to creating a new session when the
        given session_id is no longer valid (expired / not found on GTI's side).

        Returns:
            (session_id, response_markdown_text, raw_api_response_dict)
        """
        if session_id:
            try:
                return self.post_session_message(session_id, message, files)
            except GTISessionNotFoundError:
                logger.warning("[GTI] Session %s not found/expired — creating a new session.", session_id)
        return self.create_session(message, files)

# Shared client instance
gti_client = GTIAgenticClient()
