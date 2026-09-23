"""
End-to-end tests for main.py's HTTP status-code mapping — the actual
mechanism Pub/Sub acts on to decide whether to redeliver a job. A real Flask
test client is used (not a direct function call) so get_json() and the
rest of the request/response cycle run inside a genuine Flask app context,
exactly as they would when functions_framework serves this in production.
"""
import base64
import json
from datetime import datetime, timezone

import flask
import pytest

import main as worker_main


@pytest.fixture
def client():
    app = flask.Flask(__name__)

    @app.route("/", defaults={"path": ""}, methods=["GET"])
    @app.route("/<path:path>", methods=["GET", "POST", "OPTIONS"])
    def _dispatch(path):
        return worker_main.gti_bot_worker_http(flask.request)

    return app.test_client()


def _push_envelope(job_payload: dict, message_id: str = "msg-1", delivery_attempt: int = 1) -> dict:
    data = base64.b64encode(json.dumps(job_payload).encode("utf-8")).decode("ascii")
    return {
        "message": {"data": data, "messageId": message_id, "publishTime": "2024-01-01T00:00:00Z"},
        "subscription": "projects/example/subscriptions/example-sub",
        "deliveryAttempt": delivery_attempt,
    }


def _job_payload(text: str = "what is 1.1.1.1?", scope: str = "personal") -> dict:
    return {
        "kind": "message",
        "activity": {
            "type": "message",
            "id": "activity-1",
            "text": text,
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "conversation": {"id": "conv-1", "conversationType": scope},
            "from": {"id": "user-1"},
        },
        "loadingActivityId": "placeholder-1",
        "enqueuedAt": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def _mock_common(monkeypatch):
    monkeypatch.setattr("app.job_processor.claim_message", lambda message_id: True)
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: ("session-1", "**GTI**: no threats found.", None),
    )


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_successful_job_returns_200(client, monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: True)
    resp = client.post("/tasks/process", json=_push_envelope(_job_payload()))
    assert resp.status_code == 200


def test_named_gti_error_is_terminal_and_returns_200(client, monkeypatch):
    from app.gti.client import GTIRateLimitError

    def _raise(**kwargs):
        raise GTIRateLimitError("rate limited")

    monkeypatch.setattr("app.job_processor.gti_client.send_message", _raise)
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: True)

    resp = client.post("/tasks/process", json=_push_envelope(_job_payload()))
    assert resp.status_code == 200


def test_delivery_failure_returns_500_for_pubsub_redelivery(client, monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: False)
    resp = client.post("/tasks/process", json=_push_envelope(_job_payload()))
    assert resp.status_code == 500


def test_unexpected_exception_returns_500(client, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.job_processor.gti_client.send_message", _boom)
    resp = client.post("/tasks/process", json=_push_envelope(_job_payload()))
    assert resp.status_code == 500


def test_malformed_envelope_returns_500_not_400(client):
    """
    A malformed payload will fail identically on every Pub/Sub redelivery,
    so this must be a 5xx (routed to the dead-letter topic after
    max_delivery_attempts) — a 400 would ack-and-drop with no dead-letter
    visibility at all, a silent regression from Azure's behavior.
    """
    resp = client.post("/tasks/process", json={"not": "a valid pubsub envelope"})
    assert resp.status_code == 500


def test_duplicate_delivery_acks_without_reprocessing(client, monkeypatch):
    monkeypatch.setattr("app.job_processor.claim_message", lambda message_id: False)
    gti_calls = {"count": 0}
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: gti_calls.__setitem__("count", gti_calls["count"] + 1) or ("s", "r", None),
    )

    resp = client.post("/tasks/process", json=_push_envelope(_job_payload(), message_id="dup-1"))

    assert resp.status_code == 200
    assert gti_calls["count"] == 0


def test_poison_route_acks_200_even_on_internal_failure(client, monkeypatch):
    """No further dead-letter exists for a poison-delivery failure — see
    terraform/main.tf's dead_letter_subscription, which has no
    dead_letter_policy of its own. Redelivering would only loop forever."""
    def _boom(raw_payload):
        raise RuntimeError("Teams API outage")

    # main.py imported process_poison_job by name (`from app.poison_handler
    # import process_poison_job`) — _handle_poison() calls its OWN bound
    # name, so the patch target is main.process_poison_job, not
    # app.poison_handler.process_poison_job.
    monkeypatch.setattr("main.process_poison_job", _boom)
    resp = client.post("/tasks/poison", json=_push_envelope(_job_payload()))
    assert resp.status_code == 200


def test_poison_route_with_malformed_envelope_still_acks_200(client):
    resp = client.post("/tasks/poison", json={"not": "a valid pubsub envelope"})
    assert resp.status_code == 200
