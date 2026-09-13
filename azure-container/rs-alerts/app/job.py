"""
Core RS Alerts job: fetch incremental GTI alerts and deliver them to Teams.

Flow:
  1. Load the cursor (last seen ``audit.update_time``) from blob state, plus
     the set of alert ids already sent at that exact timestamp (needed to
     avoid re-delivering a same-timestamp tie — see step 4).
       - First run / no state -> backfill from BACKFILL_DAYS ago (default 7,
         clamped to 1-7) rather than the project's entire history.
  2. Ensure the bot's Teams app is installed in the target team (Microsoft
     Graph auto-install — only possible when TEAMS_CHANNEL_ID is the full
     channel link, since that's what carries the team id).
  3. Exchange the GTI API key for a short-lived bearer token.
  4. Call List Alerts with a filter combining the cursor (inclusive, ``>=``)
     AND the level filters, ordered by ``audit.update_time asc``, paginating
     through all pages. An alert re-fetched only because of that inclusive
     bound — one already recorded as sent at the cursor's exact timestamp —
     is skipped rather than re-delivered.
  5. For each remaining alert, post an Adaptive Card (v1.4) to the Teams
     channel via the Bot Framework Connector API (retrying transient
     failures), checkpointing the cursor and that alert's id after every
     successful send.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.cards import alert_id
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
            ("TEAMS_CHANNEL_ID", settings.teams_channel_id),
            ("GTI_API_KEY", settings.gti_api_key),
            ("GTI_RSA_PROJECT", settings.gti_rsa_project),
        ) if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

    if not settings.managed_identity_client_id:
        raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

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

    team_id = extract_team_id(settings.teams_channel_id)
    if team_id:
        ensure_app_installed(team_id, settings)
    else:
        logger.info(
            "TEAMS_CHANNEL_ID has no groupId (a bare channel ID was given, not the "
            "full link) — skipping Teams app auto-install; the bot must already be "
            "a member of the target team for delivery to succeed."
        )

    cursor, sent_ids_at_cursor = read_cursor(settings)
    if cursor:
        logger.info(
            "Resuming from cursor: %s (%d alert(s) already sent at that exact timestamp)",
            cursor, len(sent_ids_at_cursor),
        )
    else:
        days = _backfill_days(settings)
        cursor = _to_rfc3339(_now_utc() - timedelta(days=days))
        sent_ids_at_cursor = set()
        logger.info("No prior state found — backfilling from %s (%d day(s)).", cursor, days)

    filter_str = build_filter(cursor, settings)
    logger.info("Alert filter: %s", filter_str)

    newest_str = cursor

    def checkpoint(update_time: str, sent_alert_id: str) -> None:
        """
        Advance the cursor, tracking every alert id already sent at the
        current boundary timestamp. Needed because the List Alerts filter
        is inclusive (see build_filter) to avoid permanently dropping an
        alert that ties on audit.update_time with another one — this is
        how a re-fetched, already-sent boundary alert is later recognized
        and skipped instead of being re-delivered.
        """
        nonlocal newest_str, sent_ids_at_cursor
        if update_time != newest_str:
            sent_ids_at_cursor = set()
        sent_ids_at_cursor.add(sent_alert_id)
        write_cursor(settings, update_time, sent_ids_at_cursor)
        newest_str = update_time
        logger.info("Checkpoint saved: %s (alert %s)", update_time, sent_alert_id)

    sender = AlertSender(settings, channel_id, on_checkpoint=checkpoint)

    gti_token = get_gti_access_token(settings.gti_api_key)
    logger.info("GTI token acquired. Fetching alerts...")

    count = 0
    skipped = 0
    for alert in list_alerts(gti_token, settings.gti_rsa_project, filter_str, settings.page_size):
        audit = alert.get("audit", {})
        alert_update_time = audit.get("updateTime") or audit.get("createTime")
        if alert_update_time == cursor and alert_id(alert) in sent_ids_at_cursor:
            # Re-fetched only because of the inclusive (>=) boundary filter
            # — already sent in a prior run at this exact timestamp.
            skipped += 1
            continue
        sender.send(alert)
        count += 1

    logger.info(
        "Done — %d alert(s) sent to Teams channel, %d already-sent boundary alert(s) skipped.",
        count, skipped,
    )
    return {
        "fetched": count,
        "skipped_duplicate_boundary": skipped,
        "cursor_from": cursor,
        "cursor_to": newest_str,
    }
