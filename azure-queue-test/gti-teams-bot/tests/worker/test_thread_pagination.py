"""
Regression test for finding #6: fetch_thread_messages() must return the
actual NEWEST replies once a channel thread exceeds 250 total replies (5
pages x $top=50), not the oldest 250. The fix adds
$orderby=createdDateTime desc to the /replies query (matching the pattern
already used in attachments.py's own Graph replies fetch) so the capped
5-page pagination walks backward from the newest reply instead of forward
from the oldest.
"""
from datetime import datetime, timedelta

from app.teams import thread as thread_module

_TOTAL_REPLIES = 300
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
    Simulates Microsoft Graph's /replies pagination. Root n=0 is the oldest
    message of all. Replies are n=1 (oldest) .. n=300 (newest). Honors
    $orderby=createdDateTime desc when present in the URL — exactly like the
    real Graph API does — so this only produces the newest-first ordering
    the fix depends on if the fix is actually requesting it.
    """
    state = {"page": 0}

    def fake_get(url: str, **kwargs):
        requested_urls.append(url)
        if "/replies" not in url:
            return _FakeResponse(200, _reply(0))

        desc = "createdDateTime desc" in url or "createdDateTime+desc" in url
        page = state["page"]
        state["page"] += 1

        if desc:
            start = _TOTAL_REPLIES - page * _PAGE_SIZE
            ids = list(range(start, start - _PAGE_SIZE, -1))
        else:
            start = 1 + page * _PAGE_SIZE
            ids = list(range(start, start + _PAGE_SIZE))
        ids = [i for i in ids if 1 <= i <= _TOTAL_REPLIES]

        next_page_start_ok = (page + 1) * _PAGE_SIZE < _TOTAL_REPLIES
        next_link = f"https://graph.microsoft.com/v1.0/fake?page={page + 1}" if next_page_start_ok else None
        return _FakeResponse(200, {"value": [_reply(i) for i in ids], "@odata.nextLink": next_link})

    return fake_get


def test_returns_true_newest_replies_when_thread_exceeds_250(monkeypatch):
    requested_urls: list = []
    monkeypatch.setattr(thread_module, "graph_client", type("_G", (), {"get": staticmethod(_make_fake_get(requested_urls))})())

    result = thread_module.fetch_thread_messages(
        team_id="team1", channel_id="chan1", thread_id="thread1", limit=5, bot_app_id="bot1",
    )

    ids = [m["id"] for m in result]
    assert ids == ["r296", "r297", "r298", "r299", "r300"], (
        "Expected the true newest 5 replies out of 300 total — got the oldest-page "
        "slice instead, meaning the /replies query is missing $orderby=createdDateTime desc."
    )

    replies_urls = [u for u in requested_urls if "/replies" in u]
    assert replies_urls, "Test setup error: no /replies requests were captured."
    assert all("createdDateTime desc" in u for u in replies_urls), (
        "fetch_thread_messages() must request $orderby=createdDateTime desc on /replies."
    )
