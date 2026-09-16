"""
Google Threat Intelligence (GTI) Agentic API Client — PERFORMANCE-TEST STUB.

This is azure-queue-test's one deliberate difference from azure-queue: this
client makes NO real network call to GTI at all. send_message() sleeps for
settings.gti_timeout_seconds (default 180s, simulating GTI's real processing
latency) and then returns a fixed, canned response — the same output no
matter what the query was. Everything else in this repo (ingest, the
Storage Queue hand-off, session/output-format persistence, delivery,
Adaptive Card rendering, error handling) is untouched, so this exists purely
to load-test the queue architecture itself — ingest -> Storage Queue ->
worker -> delivery — under many concurrent/queued jobs with a known, fixed
per-job latency, without depending on GTI's real latency, rate limits, or
API cost.

The public surface (constructor kwargs, send_message()'s signature and
return shape, every exception class) is kept identical to azure-queue's real
client so job_processor.py needs no changes at all.
"""
import logging
import time
import uuid
from typing import Any

from app.config import settings

logger = logging.getLogger("gti-teams-bot")


# ── Custom Exceptions ─────────────────────────────────────────────────────────
# Kept identical to (and never raised by) the real client — job_processor.py
# imports all of these by name, so removing any would break that import.

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
    """Raised when the GTI Agentic API returns HTTP 200 but no displayable AGENT_FINAL_RESPONSE text was found."""


# ── Fixed test response ───────────────────────────────────────────────────────

_FIXED_RESPONSE_TEXT = (
    "## Test Response (azure-queue-test stub)\n\n"
    "This is a fixed, canned response from the performance-test GTI stub — "
    "no real Google Threat Intelligence API call was made. This exact text "
    "is returned for every query, regardless of what was asked.\n\n"
    "**Indicator:** 8.8.8.8\n\n"
    "**Verdict:** Benign (simulated test data)\n\n"
    "**Confidence:** N/A — this is not a real GTI analysis."
)


# ── GTI Agentic API Client (stub) ────────────────────────────────────────────

class GTIAgenticClient:
    """Performance-test stand-in for the real Google Threat Intelligence Agentic API client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_delay: float | None = None,
        rate_limit_retry_delay: float | None = None,
    ) -> None:
        # Kept for constructor-signature parity with the real client — none
        # of these are used, since there's no real request to make, retry,
        # or authenticate.
        self.api_key = api_key if api_key is not None else settings.gti_api_key
        self.base_url = base_url if base_url is not None else settings.gti_api_base_url
        # This IS used: it's the simulated processing delay below, so
        # settings.gti_timeout_seconds (180s by default in this test copy)
        # controls how long each job takes to "process".
        self.timeout = timeout if timeout is not None else settings.gti_timeout_seconds

    def close(self) -> None:
        """No real connection to close — kept for interface parity."""

    def send_message(
        self,
        message: str,
        session_id: str | None = None,
        files: list[tuple[str, bytes, str]] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Stub equivalent of the real client's send_message(): ignores
        `message`/`files` entirely, sleeps for self.timeout seconds
        (simulating GTI's real processing latency), then returns a fixed
        canned response. Reuses `session_id` when given (so channel-thread
        session continuity in session_store.py still behaves the same way),
        otherwise mints a fake one — there's no real session on GTI's side
        either way.

        Returns:
            (session_id, response_markdown_text, raw_api_response_dict)
        """
        result_session_id = session_id or f"stub-session-{uuid.uuid4()}"
        logger.info(
            "[GTI-STUB] Simulating a %.0fs GTI Agentic query (no real API call) | session_id=%s",
            self.timeout, result_session_id,
        )
        time.sleep(self.timeout)
        logger.info("[GTI-STUB] Returning fixed test response | session_id=%s", result_session_id)
        return result_session_id, _FIXED_RESPONSE_TEXT, {"stub": True}


# Shared client instance
gti_client = GTIAgenticClient()
