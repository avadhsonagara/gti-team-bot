"""
Round-trip contract test between bot-ingest-function/app/queue_job.py
(producer: build_job_payload) and bot-worker-function/app/queue_job.py
(consumer: parse_job_payload) — the one place the two apps' otherwise-
duplicated code deliberately differs (see finding #10), since it's a
producer/consumer pair, not a literal copy. Both apps define a top-level
"app" package, so they can't both be imported as "app.queue_job" in the
same process — each copy is loaded here under its own private module name
via importlib, without touching either app's own sys.path/tests.
"""
import importlib.util
import pathlib
import sys

import pytest

_GTI_TEAMS_BOT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_module(unique_name: str, file_path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(unique_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


ingest_queue_job = _load_module(
    "ingest_queue_job", _GTI_TEAMS_BOT_ROOT / "bot-ingest-function" / "app" / "queue_job.py",
)
worker_queue_job = _load_module(
    "worker_queue_job", _GTI_TEAMS_BOT_ROOT / "bot-worker-function" / "app" / "queue_job.py",
)


def test_build_then_parse_round_trip_preserves_activity_and_placeholder_id():
    activity_body = {"type": "message", "id": "a1", "text": "hello", "conversation": {"id": "c1"}}
    payload = ingest_queue_job.build_job_payload(activity_body, loading_activity_id="placeholder-1")

    parsed_activity, loading_activity_id, enqueued_at = worker_queue_job.parse_job_payload(payload)

    assert parsed_activity == activity_body
    assert loading_activity_id == "placeholder-1"
    assert enqueued_at.tzinfo is not None


def test_build_then_parse_round_trip_with_no_placeholder():
    activity_body = {"type": "message", "id": "a2", "text": "hi", "conversation": {"id": "c2"}}
    payload = ingest_queue_job.build_job_payload(activity_body, loading_activity_id=None)

    parsed_activity, loading_activity_id, _ = worker_queue_job.parse_job_payload(payload)

    assert parsed_activity == activity_body
    assert loading_activity_id is None


def test_worker_rejects_a_payload_missing_the_activity_object():
    with pytest.raises(worker_queue_job.InvalidJobPayload):
        worker_queue_job.parse_job_payload({"loadingActivityId": "x", "enqueuedAt": "2024-01-01T00:00:00Z"})
