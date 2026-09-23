"""
Installation removal event handler.

Handles bot uninstallation events from Teams channels, deleting stored GTI
session state for the affected team from Firestore.
"""
import logging

from app.gti.session_store import delete_team_sessions, get_team_id_for_channel
from app.queue_job import InvalidJobPayload
from app.teams.activity import parse_activity
from app.teams.thread import get_channel_id, get_team_id

logger = logging.getLogger("gti-teams-bot")


def process_installation_removed(raw_payload: dict) -> None:
    """
    Process an installation removal job and purge team sessions.

    Args:
        raw_payload: Decoded job dictionary (already unwrapped from Pub/Sub)
            of the installationUpdateRemove job.

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
            # channelData.team isn't always present on an installationUpdate
            # activity — fall back to a team_id cached from an earlier
            # message in the same channel (same fallback
            # app/teams/thread.py::get_thread_context uses).
            channel_id = get_channel_id(activity)
            team_id = get_team_id_for_channel(channel_id) if channel_id else ""
        if not team_id:
            logger.warning("[INSTALL] Could not resolve team_id for an installationUpdate removal — nothing to clean up.")
            return
        logger.info("[INSTALL] App removed from team=%s — deleting stored sessions across all channels.", team_id)
        delete_team_sessions(team_id)
    except InvalidJobPayload:
        raise
    except Exception:
        logger.exception("[INSTALL] Failed to process installationUpdate removal.")
