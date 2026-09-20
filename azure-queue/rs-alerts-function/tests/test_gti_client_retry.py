"""
Regression tests for gti_client.py's retry-with-backoff on GTI API calls
(token exchange and List Alerts) — same 3-attempt/exponential-backoff shape
as sender.py's _post_with_retry and state_store.py's write_cursor retry.
"""
import pytest
import requests

from app import gti_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(gti_client.time, "sleep", lambda seconds: None)


def test_token_exchange_retries_on_5xx_then_succeeds(monkeypatch):
    responses = [
        _FakeResponse(503),
        _FakeResponse(200, {"access_token": "tok-123"}),
    ]
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    monkeypatch.setattr(gti_client.requests, "request", fake_request)

    token = gti_client.get_gti_access_token("api-key")

    assert token == "tok-123"
    assert calls["n"] == 2


def test_token_exchange_retries_on_429_then_succeeds(monkeypatch):
    responses = [_FakeResponse(429), _FakeResponse(429), _FakeResponse(200, {"access_token": "tok-456"})]
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    monkeypatch.setattr(gti_client.requests, "request", fake_request)

    token = gti_client.get_gti_access_token("api-key")

    assert token == "tok-456"
    assert calls["n"] == 3


def test_token_exchange_fails_fast_on_non_retryable_4xx(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return _FakeResponse(401)

    monkeypatch.setattr(gti_client.requests, "request", fake_request)

    with pytest.raises(requests.exceptions.HTTPError):
        gti_client.get_gti_access_token("api-key")

    assert calls["n"] == 1, "a permanent auth error must not be retried"


def test_token_exchange_raises_after_exhausting_all_retries(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return _FakeResponse(500)

    monkeypatch.setattr(gti_client.requests, "request", fake_request)

    with pytest.raises(requests.exceptions.HTTPError):
        gti_client.get_gti_access_token("api-key")

    assert calls["n"] == 3


def test_token_exchange_retries_on_network_exception_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("boom")
        return _FakeResponse(200, {"access_token": "tok-789"})

    monkeypatch.setattr(gti_client.requests, "request", fake_request)

    token = gti_client.get_gti_access_token("api-key")

    assert token == "tok-789"
    assert calls["n"] == 2


def test_list_alerts_retries_a_flaky_page_then_continues(monkeypatch):
    responses = [
        _FakeResponse(503),
        _FakeResponse(200, {"alerts": [{"name": "a1"}], "nextPageToken": None}),
    ]
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    monkeypatch.setattr(gti_client.requests, "request", fake_request)

    result = list(gti_client.list_alerts("token", "proj", "filter", page_size=50))

    assert [a["name"] for a in result] == ["a1"]
    assert calls["n"] == 2
