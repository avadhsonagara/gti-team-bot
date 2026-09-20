"""Regression tests for List Alerts pagination — must follow nextPageToken until absent."""
from app.gti_client import list_alerts


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_pagination_follows_next_page_token_until_absent(monkeypatch):
    pages = [
        {"alerts": [{"name": "a1"}, {"name": "a2"}], "nextPageToken": "page-2"},
        {"alerts": [{"name": "a3"}], "nextPageToken": None},
    ]
    calls = []

    def fake_request(method, url, headers=None, params=None, timeout=None):
        assert method == "GET"
        calls.append(dict(params))
        return _FakeResponse(pages[len(calls) - 1])

    monkeypatch.setattr("app.gti_client.requests.request", fake_request)

    result = list(list_alerts("token", "proj", "some filter", page_size=50))

    assert [a["name"] for a in result] == ["a1", "a2", "a3"]
    assert len(calls) == 2
    assert "pageToken" not in calls[0]
    assert calls[1]["pageToken"] == "page-2"
    # orderBy is load-bearing for checkpoint correctness — must always be requested.
    assert calls[0]["orderBy"] == "audit.update_time asc"


def test_empty_alerts_page_stops_without_error(monkeypatch):
    monkeypatch.setattr(
        "app.gti_client.requests.request",
        lambda *a, **kw: _FakeResponse({"alerts": [], "nextPageToken": None}),
    )
    assert list(list_alerts("token", "proj", "filter", page_size=50)) == []
