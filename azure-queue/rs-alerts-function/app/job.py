"""
Core RS Alerts synchronization job module.

Orchestrates incremental GTI alert retrieval, authentication, Teams bot
installation verification via Microsoft Graph, message delivery as Adaptive Cards,
and persistent cursor checkpointing in Azure Blob Storage.
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
    """
    Get the current UTC date and time.

    Returns:
        Current datetime with UTC timezone.
    """
    return datetime.now(timezone.utc)


def _to_rfc3339(dt: datetime) -> str:
    """
    Format a datetime object into an RFC 3339 UTC string.

    Args:
        dt: The datetime instance to format.

    Returns:
        Formatted UTC timestamp string (e.g. '2026-09-21T00:00:00Z').
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_settings(settings: Settings) -> str:
    """
    Validate mandatory configuration settings and extract the destination channel ID.

    Args:
        settings: Application settings instance to inspect.

    Returns:
        Canonical Microsoft Teams channel identifier string.

    Raises:
        RuntimeError: If any required settings or credentials are unset.
    """
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
    """
    Validate and clamp the backfill window duration within permitted bounds (1-7 days).

    Args:
        settings: Application settings containing configured backfill days.

    Returns:
        Validated number of backfill days.
    """
    days = settings.backfill_days
    if not (_MIN_BACKFILL_DAYS <= days <= _MAX_BACKFILL_DAYS):
        logger.warning(
            "[RS-ALERTS CONFIG] BACKFILL_DAYS=%d is out of range (%d-%d) — defaulting to %d days.",
            days, _MIN_BACKFILL_DAYS, _MAX_BACKFILL_DAYS, _DEFAULT_BACKFILL_DAYS,
        )
        return _DEFAULT_BACKFILL_DAYS
    return days


def run_job(settings: Settings) -> dict:
    """
    Execute the incremental RS alerts synchronization pipeline.

    Coordinates cursor loading, Graph app check, token exchange, query filtering,
    batch fetching from GTI, card delivery to Teams, and incremental checkpointing.

    Args:
        settings: Application settings instance.

    Returns:
        Dictionary summarizing execution metrics:
            - 'fetched': Total number of alerts delivered.
            - 'cursor_from': Starting cursor timestamp.
            - 'cursor_to': Final advanced cursor timestamp.

    Raises:
        Exception: Re-raises any critical failure occurring during execution.
    """
    start = time.perf_counter()
    logger.info("[RS-ALERTS START] RS Alerts synchronization job started.")

    try:
        channel_id = _validate_settings(settings)
        logger.info("[RS-ALERTS CONFIG] Settings validated. Destination channel ID: %s", channel_id)

        team_id = extract_team_id(settings.teams_channel_link_or_id)
        if team_id:
            logger.info("[RS-ALERTS TEAMS-APP] Checking Teams app installation for team_id=%s...", team_id)
            ensure_app_installed(team_id, settings)
        else:
            logger.info(
                "[RS-ALERTS TEAMS-APP] TEAMS_CHANNEL_LINK_OR_ID contains no groupId (bare channel ID provided). "
                "Skipping Graph auto-install check."
            )

        cursor = read_cursor(settings)
        if cursor:
            logger.info("[RS-ALERTS CURSOR] Resuming from existing cursor timestamp: %s", cursor)
        else:
            days = _backfill_days(settings)
            cursor = _to_rfc3339(_now_utc() - timedelta(days=days))
            logger.info(
                "[RS-ALERTS CURSOR] No previous cursor found — backfilling from %s (%d day(s)).",
                cursor, days,
            )

        filter_str = build_filter(cursor, settings)
        logger.info("[RS-ALERTS FILTER] Formulated GTI alert filter: %s", filter_str)

        newest_str = cursor

        def checkpoint(update_time: str) -> None:
            """
            Persist updated cursor timestamp after successful alert delivery.

            Args:
                update_time: Update timestamp of the delivered alert.
            """
            nonlocal newest_str
            write_cursor(settings, update_time)
            newest_str = update_time
            logger.info("[RS-ALERTS CHECKPOINT] Cursor checkpoint advanced to: %s", update_time)

        sender = AlertSender(settings, channel_id, on_checkpoint=checkpoint)

        logger.info("[RS-ALERTS AUTH] Requesting GTI bearer access token...")
        gti_token = get_gti_access_token(settings.gti_api_key)
        logger.info("[RS-ALERTS AUTH] GTI access token acquired successfully.")

        logger.info("[RS-ALERTS FETCH] Querying GTI alerts and delivering to Microsoft Teams...")
        count = 0
        for alert in list_alerts(gti_token, settings.gti_rsa_project, filter_str, settings.page_size):
            sender.send(alert)
            count += 1

        elapsed = time.perf_counter() - start
        logger.info(
            "[RS-ALERTS DONE] Job completed successfully: %d alert(s) sent in %.2fs | cursor: %s -> %s",
            count, elapsed, cursor, newest_str,
        )
        return {
            "fetched": count,
            "cursor_from": cursor,
            "cursor_to": newest_str,
        }
    except Exception:
        elapsed = time.perf_counter() - start
        logger.exception("[RS-ALERTS FAILED] Job execution aborted after %.2fs due to error.", elapsed)
        raise
