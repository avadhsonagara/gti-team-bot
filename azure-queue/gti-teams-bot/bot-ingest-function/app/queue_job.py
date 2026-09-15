"""
Wire format of the job handed from this Ingest Function to the Worker
Function via the Storage Queue named by settings.job_queue_name.

Kept in sync (by hand) with bot-worker-function/app/queue_job.py — the two
Function Apps are deployed and versioned independently (no shared package),
so both copies must be updated together if this shape ever changes.

Schema:
{
  "kind": str,                       # "message" (default — a GTI query job)
                                     # or "installationUpdateRemove" (the bot
                                     # was uninstalled from a team; the
                                     # worker deletes that team's stored GTI
                                     # sessions instead of running a query).
  "activity": {...},                # the raw inbound Bot Framework Activity
                                     # JSON exactly as POSTed by the Bot
                                     # Framework Connector — the worker
                                     # re-parses it with the same
                                     # app.teams.activity.parse_activity()
                                     # this function itself uses.
  "loadingActivityId": str | None,  # id of the placeholder message this
                                     # function already posted, or None if
                                     # posting it failed (worker falls back
                                     # to a fresh send instead of an edit).
  "enqueuedAt": str,                 # ISO 8601 UTC timestamp — lets the
                                     # worker detect and skip a stale job
                                     # (settings.max_job_age_seconds).
}
"""
from datetime import datetime, timezone
from typing import Optional


def build_job_payload(activity_body: dict, loading_activity_id: Optional[str], kind: str = "message") -> dict:
    return {
        "kind": kind,
        "activity": activity_body,
        "loadingActivityId": loading_activity_id,
        "enqueuedAt": datetime.now(timezone.utc).isoformat(),
    }
