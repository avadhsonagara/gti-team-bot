"""
Regression test: fetch_thread_messages() must sort by PARSED datetime, not
by raw createdDateTime string. Microsoft Graph omits the fractional-seconds
component when it's exactly zero (e.g. "...T10:00:00Z" instead of
"...T10:00:00.000Z"), so a plain string sort places a message with
fractional seconds (".500Z") before one without any ("...:00Z") within the
same whole second, because '.' (46) sorts before 'Z' (90) in ASCII — even
when the fractional one actually happened later.
"""
from app.teams import thread as thread_module


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def test_messages_with_and_without_fractional_seconds_sort_chronologically(monkeypatch):
    root = {
        "id": "root",
        "createdDateTime": "2026-01-01T10:00:00Z",  # no fractional seconds
        "from": {"user": {"displayName": "Alice"}},
        "body": {"content": "the opening post"},
    }
    # Deliberately returned out of chronological order, so the test can't
    # accidentally pass just because Graph happened to hand them back sorted.
    reply_5s_later = {
        "id": "reply-5s-later",
        "createdDateTime": "2026-01-01T10:00:05Z",  # 5 whole seconds later, no fractional seconds
        "from": {"user": {"displayName": "Bob"}},
        "body": {"content": "a later reply"},
    }
    reply_500ms_later = {
        "id": "reply-500ms-later",
        "createdDateTime": "2026-01-01T10:00:00.500Z",  # 500ms after root, same whole second
        "from": {"user": {"displayName": "Carol"}},
        "body": {"content": "an immediate reply"},
    }

    def fake_get(url: str, **kwargs):
        if "/replies" not in url:
            return _FakeResponse(200, root)
        return _FakeResponse(200, {"value": [reply_5s_later, reply_500ms_later], "@odata.nextLink": None})

    monkeypatch.setattr(thread_module, "graph_client", type("_G", (), {"get": staticmethod(fake_get)})())

    result = thread_module.fetch_thread_messages(
        team_id="team1", channel_id="chan1", thread_id="root", limit=10, bot_app_id="bot1",
    )

    ids = [m["id"] for m in result]
    assert ids == ["root", "reply-500ms-later", "reply-5s-later"], (
        f"expected chronological order (root, then +500ms, then +5s), got {ids} — "
        "a raw string sort on createdDateTime would incorrectly place "
        "'reply-500ms-later' before 'root' since '.' < 'Z' in ASCII."
    )
