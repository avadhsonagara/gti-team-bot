"""
Core RS Alerts job: fetch incremental GTI alerts and deliver them to Teams.

Flow:
  1. Load the cursor from Firestore (or backfill from BACKFILL_DAYS ago on
     first run).
  2. Exchange the GTI API key for a bearer token.
  3. List Alerts changed strictly after the cursor, filtered by the
     configured level filters.
  4. Post each alert as an Adaptive Card to Teams, checkpointing the cursor
     in Firestore after every successful send.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.gti_client import build_filter, get_gti_access_token, list_alerts
from app.sender import AlertSender, extract_channel_id
from app.state_store import read_cursor, write_cursor

logger = logging.getLogger("rs-alerts")

_MIN_BACKFILL_DAYS = 1
_MAX_BACKFILL_DAYS = 7
_DEFAULT_BACKFILL_DAYS = 7


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_settings(settings: Settings) -> str:
    """Validate required configuration and return the canonical Teams channel ID."""
    missing = [
        name for name, val in (
            ("TEAMS_CHANNEL_ID", settings.teams_channel_id),
            ("GTI_API_KEY", settings.gti_api_key),
            ("GTI_RSA_PROJECT", settings.gti_rsa_project),
        ) if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required configuration setting(s): {', '.join(missing)}")

    if not (settings.client_id and settings.client_secret and settings.tenant_id):
        raise RuntimeError(
            "No Bot Framework credentials configured: set CLIENT_ID + CLIENT_SECRET + TENANT_ID."
        )

    return extract_channel_id(settings.teams_channel_id)


def _backfill_days(settings: Settings) -> int:
    """Clamp BACKFILL_DAYS to 1-7, warning (not failing) if it was out of range."""
    days = settings.backfill_days
    if not (_MIN_BACKFILL_DAYS <= days <= _MAX_BACKFILL_DAYS):
        logger.warning(
            "BACKFILL_DAYS=%d out of range (%d-%d) — defaulting to %d.",
            days, _MIN_BACKFILL_DAYS, _MAX_BACKFILL_DAYS, _DEFAULT_BACKFILL_DAYS,
        )
        return _DEFAULT_BACKFILL_DAYS
    return days


def run_job(settings: Settings) -> dict:
    """Fetch incremental GTI alerts and deliver them to the Teams channel."""
    channel_id = _validate_settings(settings)
    logger.info("Configuration validated. Target channel ID: %s", channel_id)

    cursor = read_cursor(settings)
    if cursor:
        logger.info("Resuming from Firestore cursor: %s", cursor)
    else:
        days = _backfill_days(settings)
        cursor = _to_rfc3339(_now_utc() - timedelta(days=days))
        logger.info("No prior Firestore cursor found — backfilling from %s (%d day(s)).", cursor, days)

    filter_str = build_filter(cursor, settings)
    logger.info("Alert filter: %s", filter_str)

    newest_str = cursor

    def checkpoint(update_time: str) -> None:
        """Advance the cursor in Firestore."""
        nonlocal newest_str
        write_cursor(settings, update_time)
        newest_str = update_time
        logger.info("Firestore checkpoint saved: %s", update_time)

    sender = AlertSender(settings, channel_id, on_checkpoint=checkpoint)

    gti_token = get_gti_access_token(settings.gti_api_key)
    logger.info("GTI token acquired. Fetching alerts...")

    count = 0
    for alert in list_alerts(gti_token, settings.gti_rsa_project, filter_str, settings.page_size):
        sender.send(alert)
        count += 1

    logger.info("Done — %d alert(s) sent to Teams channel.", count)
    return {
        "fetched": count,
        "cursor_from": cursor,
        "cursor_to": newest_str,
    }
