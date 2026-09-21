"""
Google Threat Intelligence (GTI) List Alerts API client.

  1. Exchange the GTI API key for a short-lived bearer token.
  2. Call List Alerts with a filter combining the incremental cursor AND the
     configured level filters, ordered by ``audit.update_time asc``,
     paginating through all pages.
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
    Call the GTI API with retry-with-backoff for transient failures
    (429/5xx/network errors). Any other 4xx (401, 403, 404, ...) is a
    permanent/config problem — a bad or expired API key, or an invalid
    project id — that retrying won't fix, so those fail immediately instead
    of wasting three attempts.
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
    """Exchange the GTI API key for a bearer access token (valid ~4 hours)."""
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
    Build one field's OR-clause. Raises if the setting resolves to no
    values — every filter dimension must include at least one level; there
    is no "disable this dimension" option.
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
    """Compose the List Alerts filter: the cursor (if any) AND every level filter."""
    clauses = []
    if updated_after:
        clauses.append(f'audit.update_time > "{updated_after}"')
    clauses.extend(_level_filter_clause(*spec, settings) for spec in _LEVEL_FILTERS)
    return " AND ".join(clauses)


def list_alerts(
    token: str, project: str, filter_str: str, page_size: int = 1000
) -> Iterator[dict]:
    """Yield alerts page by page, ordered oldest-first."""
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
