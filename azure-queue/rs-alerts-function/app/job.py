"""
Core RS Alerts job: fetch incremental GTI alerts and deliver them to Teams.

Flow:
  1. Load the cursor (last seen ``audit.update_time``) from blob state.
       - First run / no state -> backfill from BACKFILL_DAYS ago (default 7,
         clamped to 1-7) rather than the project's entire history.
  2. Ensure the bot's Teams app is installed in the target team (Microsoft
     Graph auto-install — only possible when TEAMS_CHANNEL_LINK_OR_ID is the
     full channel link, since that's what carries the team id).
  3. Exchange the GTI API key for a short-lived bearer token.
  4. Call List Alerts with a filter combining the cursor (strict, ``>``) AND
     the level filters, ordered by ``audit.update_time asc``, paginating
     through all pages.
  5. For each alert, post an Adaptive Card (v1.4) to the Teams channel via
     the Bot Framework Connector API (retrying transient failures),
     checkpointing the cursor after every successful send.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.graph_client import ensure_app_installed
from app.gti_client import build_filter, get_gti_access_token, list_alerts
from app.sender import AlertSender, extract_channel_id, extract_team_id
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
            ("TEAMS_CHANNEL_LINK_OR_ID", settings.teams_channel_link_or_id),
            ("GTI_API_KEY", settings.gti_api_key),
            ("GTI_RSA_PROJECT", settings.gti_rsa_project),
        ) if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

    if not settings.managed_identity_client_id:
        raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

    return extract_channel_id(settings.teams_channel_link_or_id)


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
    start = time.perf_counter()
    logger.info("[RS-ALERTS START] Job triggered.")

    try:
        channel_id = _validate_settings(settings)
        logger.info("[RS-ALERTS CONFIG] Configuration validated. Target channel ID: %s", channel_id)

        team_id = extract_team_id(settings.teams_channel_link_or_id)
        if team_id:
            logger.info("[RS-ALERTS TEAMS-APP] Ensuring bot's Teams app is installed for team %s...", team_id)
            ensure_app_installed(team_id, settings)
        else:
            logger.info(
                "[RS-ALERTS TEAMS-APP] TEAMS_CHANNEL_LINK_OR_ID has no groupId (a bare "
                "channel ID was given, not the full link) — skipping Teams app "
                "auto-install; the bot must already be a member of the target team for "
                "delivery to succeed."
            )

        cursor = read_cursor(settings)
        if cursor:
            logger.info("[RS-ALERTS CURSOR] Resuming from cursor: %s", cursor)
        else:
            days = _backfill_days(settings)
            cursor = _to_rfc3339(_now_utc() - timedelta(days=days))
            logger.info(
                "[RS-ALERTS CURSOR] No prior state found — backfilling from %s (%d day(s)).",
                cursor, days,
            )

        filter_str = build_filter(cursor, settings)
        logger.info("[RS-ALERTS FILTER] Alert filter: %s", filter_str)

        newest_str = cursor

        def checkpoint(update_time: str) -> None:
            """Advance the cursor."""
            nonlocal newest_str
            write_cursor(settings, update_time)
            newest_str = update_time
            logger.info("[RS-ALERTS CHECKPOINT] Checkpoint saved: %s", update_time)

        sender = AlertSender(settings, channel_id, on_checkpoint=checkpoint)

        logger.info("[RS-ALERTS AUTH] Requesting GTI access token...")
        gti_token = get_gti_access_token(settings.gti_api_key)
        logger.info("[RS-ALERTS AUTH] GTI token acquired.")

        logger.info("[RS-ALERTS FETCH] Fetching alerts from GTI and delivering to Teams...")
        count = 0
        for alert in list_alerts(gti_token, settings.gti_rsa_project, filter_str, settings.page_size):
            sender.send(alert)
            count += 1

        elapsed = time.perf_counter() - start
        logger.info(
            "[RS-ALERTS DONE] %d alert(s) sent to Teams channel in %.2fs | cursor %s -> %s",
            count, elapsed, cursor, newest_str,
        )
        return {
            "fetched": count,
            "cursor_from": cursor,
            "cursor_to": newest_str,
        }
    except Exception:
        elapsed = time.perf_counter() - start
        logger.exception("[RS-ALERTS FAILED] Job aborted after %.2fs.", elapsed)
        raise
