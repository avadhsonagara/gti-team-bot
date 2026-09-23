"""
Job serialization and Pub/Sub publish for handoff from the ingest function to
the worker function.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from google.cloud import pubsub_v1

from app.config import settings

logger = logging.getLogger("gti-teams-bot")


def build_job_payload(activity_body: dict, loading_activity_id: Optional[str], kind: str = "message") -> dict:
    """Construct a job payload dictionary to publish to Pub/Sub."""
    return {
        "kind": kind,
        "activity": activity_body,
        "loadingActivityId": loading_activity_id,
        "enqueuedAt": datetime.now(timezone.utc).isoformat(),
    }


# The PublisherClient instance and the resolved topic_path only need to be
# built once per instance lifetime — without this cache, every single
# inbound message would construct a fresh client on a cold path.
_publisher_instance: Optional[pubsub_v1.PublisherClient] = None
_topic_path_instance: Optional[str] = None
_publisher_lock = threading.Lock()


def _get_publisher() -> tuple[pubsub_v1.PublisherClient, str]:
    """Return a cached singleton (PublisherClient, topic_path) pair."""
    global _publisher_instance, _topic_path_instance
    if _publisher_instance is None:
        with _publisher_lock:
            if _publisher_instance is None:
                client = pubsub_v1.PublisherClient()
                _topic_path_instance = client.topic_path(settings.gcp_project_id, settings.pubsub_topic)
                _publisher_instance = client
    return _publisher_instance, _topic_path_instance


def publish_job(payload: dict) -> str:
    """
    Publish a job payload to the configured Pub/Sub topic.

    Returns:
        The published message id (blocks until Pub/Sub acknowledges receipt,
        matching the synchronous send_message() call this mirrors on Azure).
    """
    publisher, topic_path = _get_publisher()
    data = json.dumps(payload).encode("utf-8")
    future = publisher.publish(topic_path, data)
    return future.result()
