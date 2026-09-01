"""
Core RS Alerts job: fetch incremental GTI alerts and deliver them to Teams.

Flow:
  1. Load the cursor (last seen ``audit.update_time``) from blob state.
       - First run / no state -> backfill from ``BACKFILL_DAYS`` ago.
  2. Exchange the GTI API key for a short-lived bearer token.
  3. Call List Alerts with a filter combining the cursor AND the level
     filters, ordered by ``audit.update_time asc``, paginating through all
     pages.
  4. For each alert, post an Adaptive Card (v1.4) to the Teams channel via
     the configured incoming webhook, checkpointing the cursor after every
     successful send.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.gti_client import build_filter, get_gti_access_token, list_alerts
from app.sender import AlertSender
from app.state_store import read_cursor, write_cursor

logger = logging.getLogger("rs-alerts")


def _validate_settings(settings: Settings) -> None:
    """Validate required configuration."""
    missing = [
        name for name, val in (
            ("RS_ALERTS_WEBHOOK_URL", settings.rs_alerts_webhook_url),
            ("GTI_API_KEY", settings.gti_api_key),
            ("GTI_RSA_PROJECT", settings.gti_rsa_project),
        ) if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")


def run_job(settings: Settings) -> dict:
    """Fetch incremental GTI alerts and deliver them to the Teams channel."""
    _validate_settings(settings)
    logger.info("Configuration validated.")

    backfill_days = max(1, min(settings.backfill_days, 7))

    cursor = read_cursor(settings)
    if cursor:
        logger.info("Resuming from cursor: %s", cursor)
    else:
        cursor = (datetime.now(timezone.utc) - timedelta(days=backfill_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info("No prior state found — backfilling from %s (%d days)", cursor, backfill_days)

    filter_str = build_filter(cursor, settings)
    logger.info("Alert filter: %s", filter_str)

    newest_str = cursor

    def checkpoint(update_time: str) -> None:
        nonlocal newest_str
        write_cursor(settings, update_time)
        newest_str = update_time
        logger.info("Checkpoint saved: %s", update_time)

    sender = AlertSender(settings, on_checkpoint=checkpoint)

    gti_token = get_gti_access_token(settings.gti_api_key)
    logger.info("GTI token acquired. Fetching alerts...")

    count = 0
    for alert in list_alerts(gti_token, settings.gti_rsa_project, filter_str, settings.page_size):
        sender.send(alert)
        count += 1

    logger.info("Done — %d alert(s) sent to Teams channel.", count)
    return {"fetched": count, "cursor_from": cursor, "cursor_to": newest_str}
