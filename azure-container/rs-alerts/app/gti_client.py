"""
Google Threat Intelligence (GTI) List Alerts API client.

  1. Exchange the GTI API key for a short-lived bearer token.
  2. Call List Alerts with a filter combining the incremental cursor AND the
     configured level filters, ordered by ``audit.update_time asc``,
     paginating through all pages.
"""
import logging
from collections.abc import Iterator

import requests

from app.config import Settings

GTI_TOKEN_URL = "https://idp.prod.identity.proactive.virustotal.com/realms/master/exchange/api-key"
GTI_API_BASE = "https://threatintelligence.googleapis.com/v1beta"

logger = logging.getLogger("rs-alerts")

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
    resp = requests.post(
        GTI_TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"api_key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _level_filter_clause(
    field: str, settings_attr: str, prefix: str, valid_suffixes: tuple, settings: Settings
) -> str:
    """
    Build one field's OR-clause. Raises if the setting resolves to no
    values — every filter dimension must include at least one level; there
    is no "disable this dimension" option (matching the gti-alerts
    reference script's own validation).
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
        # Inclusive (>=), not strict (>) as the gti-alerts reference script
        # uses: two alerts can share the exact same audit.update_time, and
        # a strict > would permanently exclude whichever one wasn't sent
        # before the cursor advanced to that shared timestamp. job.py
        # skips alerts already recorded as sent at this exact timestamp
        # (see state_store.read_cursor's sent_ids_at_cursor) so this
        # inclusive bound re-fetches, rather than silently re-delivers,
        # that boundary alert. Deliberately kept even though it diverges
        # from the reference script, which has this same silent-drop gap.
        clauses.append(f'audit.update_time >= "{updated_after}"')
    clauses.extend(_level_filter_clause(*spec, settings) for spec in _LEVEL_FILTERS)
    return " AND ".join(clauses)


def list_alerts(
    token: str, project: str, filter_str: str, page_size: int = 1000
) -> Iterator[dict]:
    """Yield alerts page by page, ordered oldest-first."""
    url = f"{GTI_API_BASE}/projects/{project}/alerts"
    headers = {"Authorization": f"Bearer {token}", "x-goog-user-project": project}
    params = {"pageSize": page_size, "orderBy": "audit.update_time asc", "filter": filter_str}

    while True:
        logger.info("Calling GET %s", url)
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        page_alerts = data.get("alerts", [])
        logger.info("Fetched %d alerts on this page", len(page_alerts))
        yield from page_alerts

        next_token = data.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token
