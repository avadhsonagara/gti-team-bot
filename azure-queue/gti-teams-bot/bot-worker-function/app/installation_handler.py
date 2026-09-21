"""
Installation removal event handler.

Handles bot uninstallation events from Teams channels, deleting stored GTI session
state for the affected team from Azure Table Storage.
"""
import logging

from app.gti.session_store import delete_team_sessions
from app.queue_job import InvalidJobPayload
from app.teams.activity import parse_activity
from app.teams.thread import get_team_id

logger = logging.getLogger("gti-teams-bot")


def process_installation_removed(raw_payload: dict) -> None:
    """
    Process an installation removal job and purge team sessions.

    Args:
        raw_payload: Deserialized dictionary payload of the installationUpdate job.

    Raises:
        InvalidJobPayload: If the payload is missing a valid activity dictionary.
    """
    activity_body = raw_payload.get("activity")
    if not isinstance(activity_body, dict):
        raise InvalidJobPayload("installationUpdateRemove job is missing a valid 'activity' object.")

    try:
        activity = parse_activity(activity_body)
        team_id = get_team_id(activity)
        if not team_id:
            logger.warning("[INSTALL] Could not resolve team_id for an installationUpdate removal — nothing to clean up.")
            return
        logger.info("[INSTALL] App removed from team=%s — deleting stored sessions across all channels.", team_id)
        delete_team_sessions(team_id)
    except InvalidJobPayload:
        raise
    except Exception:
        logger.exception("[INSTALL] Failed to process installationUpdate removal.")
