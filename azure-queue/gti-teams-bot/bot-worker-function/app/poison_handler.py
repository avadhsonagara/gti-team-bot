"""
Handles a job that exhausted every retry (host.json's extensions.queues.
maxDequeueCount) and was auto-routed by the Functions runtime to the
"<job_queue_name>-poison" queue — the Azure Functions Storage extension's
own, automatic naming convention, not something this codebase creates.

Best-effort only: tells the user their request failed instead of leaving the
"looking into that…" placeholder stuck forever. Never lets an exception
escape to the caller — there is no further queue this could be routed to, so
an unhandled exception here would just be silently dropped by the runtime
anyway; catching it here at least gets it logged.
"""
import logging

from app.observability import bind_request
from app.queue_job import InvalidJobPayload, get_job_kind, parse_job_payload
from app.teams.activity import parse_activity
from app.teams.cards import build_status_card
from app.teams.context import Ctx
from app.utils.helpers import deliver_message

logger = logging.getLogger("gti-teams-bot")

_POISON_NOTICE = (
    "⚠️ **Unable to Complete Request**\n\n"
    "We encountered an unexpected issue while processing your request. Please try again in a few moments."
)


def process_poison_job(raw_payload: dict) -> None:
    if get_job_kind(raw_payload) == "installationUpdateRemove":
        # A cleanup job, not a user query — there's no placeholder to
        # replace and, having just been uninstalled, likely no conversation
        # left to post into either. Already logged by installation_handler.py
        # each time it failed; nothing further to notify anyone about.
        logger.error("[POISON] installationUpdateRemove cleanup job permanently failed after exhausting retries.")
        return

    try:
        activity_body, loading_activity_id, _ = parse_job_payload(raw_payload)
    except InvalidJobPayload:
        logger.error("[POISON] Poisoned message doesn't match the expected job shape — cannot notify the user.")
        return

    try:
        activity = parse_activity(activity_body)
        ctx = Ctx(activity)
        scope = getattr(activity.conversation, "conversation_type", "") or ""
        sender = getattr(activity, "from_", None)
        user_id = getattr(sender, "id", "unknown") if sender else "unknown"
        bind_request(request_id=activity.id or "", user=user_id, conversation=activity.conversation.id, activity_id=activity.id or "")
    except Exception:
        logger.exception("[POISON] Could not reconstruct the activity from the poisoned message — cannot notify the user.")
        return

    logger.error(
        "[POISON] Job permanently failed after exhausting retries | conversation=%s scope=%s loading_activity_id=%s",
        activity.conversation.id, scope, loading_activity_id,
    )
    try:
        delivered = deliver_message(
            ctx, loading_activity_id, _POISON_NOTICE, build_status_card(_POISON_NOTICE),
            edit_in_place=(scope == "channel"),
        )
        if delivered:
            logger.info("[POISON] Permanent-failure notice delivered to the user.")
        else:
            logger.error("[POISON] All delivery attempts for the permanent-failure notice failed.")
    except Exception:
        logger.exception("[POISON] Failed to deliver the permanent-failure notice to the user.")
