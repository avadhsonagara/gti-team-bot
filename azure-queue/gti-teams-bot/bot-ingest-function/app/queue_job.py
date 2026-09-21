"""
Queue job serialization for handoff from the ingest function to the worker function.

Constructs dictionary payloads containing activity data, placeholder references,
and enqueue timestamps.
"""
from datetime import datetime, timezone
from typing import Optional


def build_job_payload(activity_body: dict, loading_activity_id: Optional[str], kind: str = "message") -> dict:
    """
    Construct a queue job payload dictionary.

    Args:
        activity_body: Raw activity dictionary received from Teams.
        loading_activity_id: Activity ID of the posted placeholder message, if any.
        kind: Job type identifier ('message' or 'installationUpdateRemove').

    Returns:
        Dictionary representing the serialized queue job.
    """
    return {
        "kind": kind,
        "activity": activity_body,
        "loadingActivityId": loading_activity_id,
        "enqueuedAt": datetime.now(timezone.utc).isoformat(),
    }
