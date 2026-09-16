"""
Handles a Teams "app uninstalled from this team" notification
(installationUpdate / action=remove), enqueued by bot-ingest-function/ like
any other job (see app/queue_job.py's "kind" field). Deletes every stored
GTI session for that team, across every channel
(app/gti/session_store.py::delete_team_sessions), so Table Storage doesn't
accumulate rows for a team that no longer has the bot installed.

Best-effort only, same spirit as poison_handler.py: never raises. There is
no further queue this could be routed to, and nothing about a missing/
unresolvable team_id would be fixed by a retry.
"""
import logging

from app.gti.session_store import delete_team_sessions
from app.queue_job import InvalidJobPayload
from app.teams.activity import parse_activity
from app.teams.thread import get_team_id

logger = logging.getLogger("gti-teams-bot")


def process_installation_removed(raw_payload: dict) -> None:
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
