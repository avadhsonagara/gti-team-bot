"""
Regression test for finding #5: bot_client.py's shared requests.Session must
retry transient Bot Framework Connector failures (429/5xx) — but must NOT
retry POST (send_activity's method), since retrying a POST that may have
already been processed server-side risks double-posting a message to the
user. PUT/DELETE (update_activity/delete_activity) are idempotent and should
be retried.

Also regression-tests a real gap that follows directly from the above:
urllib3's allowed_methods restriction (excluding POST) blocks retrying
*every* status in status_forcelist for POST, including 429 — even though a
429 means the request was throttled before being processed at all, so
retrying it carries none of the double-post risk a 5xx does. send_activity()
handles 429 with its own dedicated retry loop instead, honoring Retry-After.
"""
import time as time_module

import pytest

from app.teams import bot_client
from app.constants import BOT_CONNECTOR_RETRY_TOTAL


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, body: bytes = b'{"id": "activity-1"}'):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = body

    def json(self):
        import json
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def test_retry_adapter_is_mounted_with_expected_status_codes():
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    retry = adapter.max_retries
    assert retry.total == 3
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}


def test_post_is_not_in_the_retried_methods():
    """send_activity() creates a new message via POST — must not be blindly retried."""
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert "POST" not in adapter.max_retries.allowed_methods


def test_put_and_delete_are_in_the_retried_methods():
    """update_activity() (PUT) and delete_activity() (DELETE) are idempotent — safe to retry."""
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert "PUT" in adapter.max_retries.allowed_methods
    assert "DELETE" in adapter.max_retries.allowed_methods


def test_connection_pool_is_sized_to_worker_concurrency():
    """
    The mounted adapter's pool must match settings.concurrent_requests
    (bicep's workerConcurrentRequests), not urllib3's default pool size of
    10 — otherwise concurrent worker threads beyond 10 discard and recreate
    connections instead of reusing a pooled one.
    """
    from app.config import settings

    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert adapter._pool_connections == settings.concurrent_requests
    assert adapter._pool_maxsize == settings.concurrent_requests


def test_send_activity_retries_429_honoring_retry_after(monkeypatch):
    responses = [
        _FakeResponse(429, headers={"Retry-After": "2"}),
        _FakeResponse(429, headers={}),  # no Retry-After this time -> falls back to backoff
        _FakeResponse(200),
    ]
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    sleeps: list = []
    monkeypatch.setattr(bot_client, "_session", type("_S", (), {"post": staticmethod(fake_post)})())
    monkeypatch.setattr(bot_client, "get_bot_token", lambda: "fake-token")
    monkeypatch.setattr(time_module, "sleep", lambda s: sleeps.append(s))

    result = bot_client.send_activity("https://smba.example/", "conv-1", {"type": "message", "text": "hi"})

    assert result == {"id": "activity-1"}
    assert calls["n"] == 3
    assert sleeps[0] == 2.0, "must honor the Retry-After header when present"
    assert sleeps[1] > 0, "must fall back to backoff when Retry-After is absent"


def test_send_activity_raises_after_exhausting_429_retries(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(429)

    monkeypatch.setattr(bot_client, "_session", type("_S", (), {"post": staticmethod(fake_post)})())
    monkeypatch.setattr(bot_client, "get_bot_token", lambda: "fake-token")
    monkeypatch.setattr(time_module, "sleep", lambda s: None)

    try:
        bot_client.send_activity("https://smba.example/", "conv-1", {})
        raise AssertionError("expected an exception after exhausting 429 retries")
    except Exception as exc:
        assert "429" in str(exc)
    assert calls["n"] == BOT_CONNECTOR_RETRY_TOTAL + 1, "expected the initial attempt plus every retry"


def test_send_activity_does_not_retry_a_5xx(monkeypatch):
    """A 5xx on POST must fail on the first attempt — retrying it risks double-posting."""
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(500)

    monkeypatch.setattr(bot_client, "_session", type("_S", (), {"post": staticmethod(fake_post)})())
    monkeypatch.setattr(bot_client, "get_bot_token", lambda: "fake-token")

    with pytest.raises(Exception, match="500"):
        bot_client.send_activity("https://smba.example/", "conv-1", {})
    assert calls["n"] == 1
