"""
Regression tests for app/teams/bot_client.py's outbound retry/pool behavior,
ported from the Azure implementation's equivalent fixes:
  - The mounted adapter's connection pool must match settings.concurrent_requests
    (reused from the THREADS env var), not urllib3's default pool size of 10.
  - PUT/DELETE (idempotent) are retried via the mounted adapter on
    429/500/502/503/504.
  - POST (send_activity, creates a new message) is deliberately excluded from
    the adapter's own retry — retrying an ambiguous 5xx risks double-posting.
    A 429 carries no such risk (the request was throttled, never processed),
    so send_activity() retries it itself via a dedicated loop honoring
    Retry-After.
"""
import time as time_module

import pytest

from app.config import settings
from app.constants import BOT_CONNECTOR_RETRY_TOTAL
from app.teams import bot_client


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
    assert retry.total == BOT_CONNECTOR_RETRY_TOTAL
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}


def test_post_is_not_in_the_retried_methods():
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert "POST" not in adapter.max_retries.allowed_methods


def test_put_and_delete_are_in_the_retried_methods():
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert "PUT" in adapter.max_retries.allowed_methods
    assert "DELETE" in adapter.max_retries.allowed_methods


def test_connection_pool_is_sized_to_concurrent_requests():
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert adapter._pool_connections == settings.concurrent_requests
    assert adapter._pool_maxsize == settings.concurrent_requests


def test_send_activity_retries_429_honoring_retry_after(monkeypatch):
    responses = [
        _FakeResponse(429, headers={"Retry-After": "2"}),
        _FakeResponse(429, headers={}),
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
    assert sleeps[0] == 2.0
    assert sleeps[1] > 0


def test_send_activity_raises_after_exhausting_429_retries(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(429)

    monkeypatch.setattr(bot_client, "_session", type("_S", (), {"post": staticmethod(fake_post)})())
    monkeypatch.setattr(bot_client, "get_bot_token", lambda: "fake-token")
    monkeypatch.setattr(time_module, "sleep", lambda s: None)

    with pytest.raises(Exception, match="429"):
        bot_client.send_activity("https://smba.example/", "conv-1", {})
    assert calls["n"] == BOT_CONNECTOR_RETRY_TOTAL + 1


def test_send_activity_does_not_retry_a_5xx(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(500)

    monkeypatch.setattr(bot_client, "_session", type("_S", (), {"post": staticmethod(fake_post)})())
    monkeypatch.setattr(bot_client, "get_bot_token", lambda: "fake-token")

    with pytest.raises(Exception, match="500"):
        bot_client.send_activity("https://smba.example/", "conv-1", {})
    assert calls["n"] == 1
