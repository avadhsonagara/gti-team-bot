"""
End-to-end tests for bot-ingest-function's main.py: the size/auth guards,
empty-query short-circuit, and the happy-path placeholder-then-publish flow.
Uses a real Flask test client (functions_framework's own HTTP surface),
matching the same pattern tests/worker/test_worker_http.py uses.
"""
import json

import flask
import pytest

import main as ingest_main


@pytest.fixture
def client():
    app = flask.Flask(__name__)

    @app.route("/", defaults={"path": ""}, methods=["GET"])
    @app.route("/<path:path>", methods=["GET", "POST", "OPTIONS"])
    def _dispatch(path):
        return ingest_main.gti_bot_ingest_http(flask.request)

    return app.test_client()


def _activity(text: str = "what is 1.1.1.1?", scope: str = "personal") -> dict:
    return {
        "type": "message",
        "id": "activity-1",
        "text": text,
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": "conv-1", "conversationType": scope},
        "from": {"id": "user-1", "name": "Alice"},
    }


@pytest.fixture(autouse=True)
def _bypass_auth(monkeypatch):
    monkeypatch.setattr(ingest_main, "validate_bot_framework_token", lambda *a, **kw: {})


@pytest.fixture(autouse=True)
def _fake_send_activity(monkeypatch):
    """Ctx.send() -> app.teams.context.send_activity (imported by name into context.py's own namespace)."""
    monkeypatch.setattr("app.teams.context.send_activity", lambda service_url, conversation_id, activity: {"id": "placeholder-1"})


def test_oversized_body_rejected_413(client, monkeypatch):
    monkeypatch.setattr(ingest_main.settings, "max_request_body_bytes", 10)
    resp = client.post("/api/messages", data=b"x" * 100, content_type="application/json")
    assert resp.status_code == 413


def test_invalid_json_body_rejected_400(client):
    resp = client.post("/api/messages", data=b"not json", content_type="application/json")
    assert resp.status_code == 400


def test_auth_failure_rejected_401(client, monkeypatch):
    from app.teams.auth import BotFrameworkAuthError

    def _reject(*a, **kw):
        raise BotFrameworkAuthError("bad token")

    monkeypatch.setattr(ingest_main, "validate_bot_framework_token", _reject)
    resp = client.post("/api/messages", json=_activity())
    assert resp.status_code == 401


def test_non_message_activity_is_a_no_op_200(client):
    resp = client.post("/api/messages", json={"type": "typing", "conversation": {"id": "conv-1"}, "serviceUrl": "https://x"})
    assert resp.status_code == 200


def test_empty_query_sends_usage_hint_without_publishing(client, monkeypatch):
    publish_calls = []
    monkeypatch.setattr(ingest_main, "publish_job", lambda payload: publish_calls.append(payload) or "msg-id")

    resp = client.post("/api/messages", json=_activity(text="   "))

    assert resp.status_code == 200
    assert publish_calls == []


def test_successful_message_posts_placeholder_and_publishes_job(client, monkeypatch):
    publish_calls = []
    monkeypatch.setattr(ingest_main, "publish_job", lambda payload: publish_calls.append(payload) or "msg-id")

    resp = client.post("/api/messages", json=_activity(text="what is 1.1.1.1?"))

    assert resp.status_code == 200
    assert len(publish_calls) == 1
    payload = publish_calls[0]
    assert payload["kind"] == "message"
    assert payload["loadingActivityId"] == "placeholder-1"
    assert payload["activity"]["text"] == "what is 1.1.1.1?"


def test_oversized_job_payload_delivers_notice_instead_of_publishing(client, monkeypatch):
    monkeypatch.setattr(ingest_main.settings, "max_job_payload_bytes", 1)
    publish_calls = []
    monkeypatch.setattr(ingest_main, "publish_job", lambda payload: publish_calls.append(payload) or "msg-id")

    resp = client.post("/api/messages", json=_activity())

    assert resp.status_code == 200
    assert publish_calls == []


def test_publish_failure_delivers_friendly_notice(client, monkeypatch):
    def _boom(payload):
        raise RuntimeError("Pub/Sub unavailable")

    monkeypatch.setattr(ingest_main, "publish_job", _boom)
    # Must not raise out of the request handler — always acks 200 so Bot
    # Framework doesn't retry (which would double-post the placeholder).
    resp = client.post("/api/messages", json=_activity())
    assert resp.status_code == 200
