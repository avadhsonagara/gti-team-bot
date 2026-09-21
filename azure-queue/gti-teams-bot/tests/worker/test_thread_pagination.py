"""
Tests for thread message pagination and ordering in Microsoft Graph API.

Verifies that fetch_thread_messages() correctly traverses pages to retrieve the newest replies
in high-activity channel threads without relying on unsupported $orderby query parameters.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.teams import thread as thread_module

_TOTAL_REPLIES = 3000  # 60 pages of 50 — exceeds THREAD_CONTEXT_MAX_PAGES (5)
_PAGE_SIZE = 50


def _created(n: int) -> str:
    return (datetime(2024, 1, 1) + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reply(n: int) -> dict:
    return {
        "id": f"r{n}",
        "createdDateTime": _created(n),
        "from": {"user": {"displayName": "Alice"}},
        "body": {"content": f"message {n}"},
    }


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def _make_fake_get(requested_urls: list):
    """
    Simulate Microsoft Graph /replies endpoint behavior with pagination.
    """
    state = {"page": 0}

    def fake_get(url: str, **kwargs):
        requested_urls.append(url)
        if "/replies" not in url:
            return _FakeResponse(200, _reply(0))

        if "orderby" in url.lower():
            return _FakeResponse(400, {"error": {"message": "Query option 'OrderBy' is not allowed."}})

        page = state["page"]
        state["page"] += 1

        start = _TOTAL_REPLIES - page * _PAGE_SIZE
        ids = [i for i in range(start, start - _PAGE_SIZE, -1) if 1 <= i <= _TOTAL_REPLIES]

        next_page_start_ok = (page + 1) * _PAGE_SIZE < _TOTAL_REPLIES
        next_link = f"https://graph.microsoft.com/v1.0/fake/replies?page={page + 1}" if next_page_start_ok else None
        return _FakeResponse(200, {"value": [_reply(i) for i in ids], "@odata.nextLink": next_link})

    return fake_get


def test_returns_true_newest_replies_without_requesting_orderby(monkeypatch):
    """Test that pagination retrieves the latest replies without requiring an orderby query param."""
    requested_urls: list = []
    monkeypatch.setattr(thread_module, "graph_client", type("_G", (), {"get": staticmethod(_make_fake_get(requested_urls))})())

    # Target near page 3 of the (unrequested, but Graph-provided) descending
    # stream — well before all 60 pages would otherwise need fetching.
    target_n = _TOTAL_REPLIES - 3 * _PAGE_SIZE
    target_timestamp = datetime.fromisoformat(_created(target_n).replace("Z", "+00:00"))

    result = thread_module.fetch_thread_messages(
        team_id="team1", channel_id="chan1", thread_id="thread1", limit=5, bot_app_id="bot1",
        target_timestamp=target_timestamp,
    )

    ids = [m["id"] for m in result]
    assert ids == [f"r{_TOTAL_REPLIES - 4}", f"r{_TOTAL_REPLIES - 3}", f"r{_TOTAL_REPLIES - 2}",
                   f"r{_TOTAL_REPLIES - 1}", f"r{_TOTAL_REPLIES}"], (
        "Expected the true newest 5 replies — pagination must have stopped short "
        "of reaching them, or sorted them incorrectly."
    )

    replies_urls = [u for u in requested_urls if "/replies" in u]
    assert replies_urls, "Test setup error: no /replies requests were captured."
    assert not any("orderby" in u.lower() for u in replies_urls), (
        "fetch_thread_messages() must NOT request $orderby on /replies — confirmed live that "
        "Graph rejects it outright with a 400 on this endpoint."
    )
    assert len(replies_urls) < 10, (
        f"expected target_timestamp-based pagination to stop well short of all 60 pages, "
        f"made {len(replies_urls)} requests — early stopping isn't engaging."
    )


def test_thread_context_works_for_a_brand_new_root_post(monkeypatch):
    """
    Verify thread context resolution when the activity is the root post of a thread.
    """
    requested_urls: list = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        if "/replies" not in url:
            # The root IS the current activity itself (id=999) — this is
            # the query that triggered this fetch, correctly excluded from
            # its own "history" below via exclude_message_id.
            return _FakeResponse(200, {
                "id": "999", "createdDateTime": "2026-01-01T00:00:00Z",
                "from": {"user": {"displayName": "Alice"}},
                "body": {"content": "the opening post"},
            })
        # A prior reply already sitting in the thread by the time this
        # (root-post) activity's own context gets fetched — must show up.
        return _FakeResponse(200, {"value": [
            {"id": "888", "createdDateTime": "2025-12-31T23:59:00Z",
             "from": {"user": {"displayName": "Bob"}}, "body": {"content": "an earlier reply"}},
        ], "@odata.nextLink": None})

    monkeypatch.setattr(thread_module, "graph_client", type("_G", (), {"get": staticmethod(fake_get)})())
    monkeypatch.setattr(thread_module, "get_team_id", lambda activity: "team-1")
    monkeypatch.setattr(thread_module, "get_channel_id", lambda activity: "channel-1")

    activity = SimpleNamespace(
        id="999",
        conversation=SimpleNamespace(id="19:abc@thread.tacv2", conversation_type="channel"),
    )

    context = thread_module.get_thread_context(activity, "channel")

    assert requested_urls, "bailed out before any Graph call — root-post thread id fallback isn't working"
    assert "an earlier reply" in context
    assert "the opening post" not in context  # excluded: it's the current activity, not history
