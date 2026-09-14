"""
Wire format of the job this Worker Function receives via its Storage Queue
trigger (queue named by settings.job_queue_name). See
bot-ingest-function/app/queue_job.py for the producer side and the full
schema comment — both copies must be updated together if this shape ever
changes (no shared package between the two Function Apps).
"""
from datetime import datetime, timezone
from typing import Optional


class InvalidJobPayload(Exception):
    """Raised when a dequeued message doesn't match the expected job shape."""


def parse_job_payload(raw: dict) -> tuple[dict, Optional[str], datetime]:
    """Returns (activity_body, loading_activity_id, enqueued_at)."""
    activity_body = raw.get("activity")
    if not isinstance(activity_body, dict):
        raise InvalidJobPayload("Job payload is missing a valid 'activity' object.")
    loading_activity_id = raw.get("loadingActivityId")
    enqueued_at = _parse_iso(raw.get("enqueuedAt")) or datetime.now(timezone.utc)
    return activity_body, loading_activity_id, enqueued_at


def _parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
