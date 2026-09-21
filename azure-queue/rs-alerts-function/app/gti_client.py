"""
Google Threat Intelligence (GTI) List Alerts API client.

Handles authentication with the GTI identity provider and queries the
Threat Intelligence Alerts API with incremental timestamp and level filters.
"""
import logging
import time
from collections.abc import Iterator

import requests

from app.config import Settings

GTI_TOKEN_URL = "https://idp.prod.identity.proactive.virustotal.com/realms/master/exchange/api-key"
GTI_API_BASE = "https://threatintelligence.googleapis.com/v1beta"

logger = logging.getLogger("rs-alerts")

_GTI_REQUEST_RETRIES = 3
_GTI_REQUEST_BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """
    Execute an HTTP request against GTI with exponential backoff on transient errors.

    Retries on network errors and HTTP status codes 429, 500, 502, 503, and 504.
    Client errors (e.g., 401, 403, 404) are raised immediately without retrying.

    Args:
        method: HTTP method (e.g., 'GET', 'POST').
        url: Request target URL.
        **kwargs: Additional arguments passed to requests.request.

    Returns:
        The successful requests.Response object.

    Raises:
        requests.RequestException: If the request fails after all retry attempts.
    """
    last_exc: Exception | None = None
    for attempt in range(_GTI_REQUEST_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == _GTI_REQUEST_RETRIES - 1:
                raise
            delay = _GTI_REQUEST_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "[GTI RETRY] Network error during request (%s) — retrying attempt %d/%d in %.1fs.",
                exc, attempt + 1, _GTI_REQUEST_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _GTI_REQUEST_RETRIES - 1:
            delay = _GTI_REQUEST_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "[GTI RETRY] HTTP %d received — retrying attempt %d/%d in %.1fs.",
                resp.status_code, attempt + 1, _GTI_REQUEST_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        resp.raise_for_status()
        return resp

    raise last_exc or RuntimeError("GTI API request failed after retries.")

_LEVEL_FILTERS = [
    ("severity_analysis.severity_level", "filter_severity_level", "SEVERITY_LEVEL_",
     ("LOW", "MEDIUM", "HIGH")),
    ("priority_analysis.priority_level", "filter_priority_level", "PRIORITY_LEVEL_",
     ("LOW", "MEDIUM", "HIGH", "CRITICAL")),
    ("relevance_analysis.relevance_level", "filter_relevance_level", "RELEVANCE_LEVEL_",
     ("LOW", "MEDIUM", "HIGH")),
    ("relevance_analysis.confidence", "filter_relevance_confidence", "CONFIDENCE_LEVEL_",
     ("LOW", "MEDIUM", "HIGH")),
]


def get_gti_access_token(api_key: str) -> str:
    """
    Exchange the GTI API key for an OAuth bearer access token.

    Args:
        api_key: The GTI secret API key.

    Returns:
        Access token string valid for API operations.

    Raises:
        requests.RequestException: If token exchange fails.
    """
    resp = _request_with_retry(
        "POST",
        GTI_TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"api_key": api_key},
        timeout=30,
    )
    return resp.json()["access_token"]


def _level_filter_clause(
    field: str, settings_attr: str, prefix: str, valid_suffixes: tuple, settings: Settings
) -> str:
    """
    Build an individual filter clause for a GTI alert level dimension.

    Parses comma-separated level values from settings, validates prefixes and allowed
    suffixes, and constructs an equality or disjunction clause.

    Args:
        field: GTI API filter attribute path (e.g. 'severity_analysis.severity_level').
        settings_attr: Attribute name on Settings holding configured filter values.
        prefix: Standard enum prefix (e.g. 'SEVERITY_LEVEL_').
        valid_suffixes: Tuple of permitted enum suffix strings.
        settings: Application settings instance.

    Returns:
        Filter clause string for the specified dimension.

    Raises:
        RuntimeError: If values are invalid or no values resolve for the dimension.
    """
    raw = getattr(settings, settings_attr)
    values = []
    for part in raw.split(","):
        part = part.strip().upper()
        if not part:
            continue
        full = part if part.startswith(prefix) else f"{prefix}{part}"
        if full[len(prefix):] not in valid_suffixes:
            raise RuntimeError(
                f"{settings_attr}={part!r} is invalid; valid values: {', '.join(valid_suffixes)}"
            )
        if full not in values:
            values.append(full)
    if not values:
        raise RuntimeError(f"{settings_attr} resolved to no values.")
    clause = " OR ".join(f'{field} = "{v}"' for v in values)
    return f"({clause})" if len(values) > 1 else clause


def build_filter(updated_after: str | None, settings: Settings) -> str:
    """
    Compose the complete query filter for the GTI List Alerts endpoint.

    Combines optional incremental cursor timestamp constraints with configured
    severity, priority, relevance, and confidence level filters.

    Args:
        updated_after: Optional RFC 3339 timestamp string; only alerts updated after this are returned.
        settings: Application settings containing level filter configurations.

    Returns:
        A combined query filter string.
    """
    clauses = []
    if updated_after:
        clauses.append(f'audit.update_time > "{updated_after}"')
    clauses.extend(_level_filter_clause(*spec, settings) for spec in _LEVEL_FILTERS)
    return " AND ".join(clauses)


def list_alerts(
    token: str, project: str, filter_str: str, page_size: int = 1000
) -> Iterator[dict]:
    """
    Yield alerts page by page from the GTI API ordered chronologically.

    Handles pagination automatically via `pageToken` until all matching alerts
    have been fetched.

    Args:
        token: Bearer access token for GTI API.
        project: Google Cloud project ID associated with GTI.
        filter_str: Filter query string applied to the alerts endpoint.
        page_size: Number of alerts to request per page.

    Yields:
        Individual alert resource dictionaries.

    Raises:
        requests.RequestException: If an API page request fails.
    """
    url = f"{GTI_API_BASE}/projects/{project}/alerts"
    headers = {"Authorization": f"Bearer {token}", "x-goog-user-project": project}
    params = {"pageSize": page_size, "orderBy": "audit.update_time asc", "filter": filter_str}

    page_num = 0
    total_fetched = 0
    while True:
        page_num += 1
        logger.info("[GTI FETCH] Requesting page %d from %s", page_num, url)
        resp = _request_with_retry("GET", url, headers=headers, params=params, timeout=60)
        data = resp.json()

        page_alerts = data.get("alerts", [])
        total_fetched += len(page_alerts)
        logger.info(
            "[GTI FETCH] Page %d returned %d alert(s) (%d total accumulated so far).",
            page_num, len(page_alerts), total_fetched,
        )
        yield from page_alerts

        next_token = data.get("nextPageToken")
        if not next_token:
            logger.info("[GTI FETCH] Pagination complete: %d alert(s) fetched across %d page(s).", total_fetched, page_num)
            break
        params["pageToken"] = next_token
