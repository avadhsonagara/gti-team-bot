"""
Regression tests for the channel-attachment fix: Bot Framework never
includes a usable content_url on a channel message (confirmed live, even
for a freshly-uploaded file, not just an existing SharePoint/OneDrive
share) — so download_attachments() must fall through to the Microsoft
Graph fallback for ANY channel attachment, and that fallback must accept
any Graph attachment with a contentUrl, not just ones whose contentType is
literally "reference" (which only covers the "user shares an existing
file" case, not a fresh channel upload).

Also covers the later id-equivalence fix: a channel OR group chat
message's activity.id IS its Graph chatMessage.id (confirmed live for a
channel thread's root post, a channel reply, and a group chat message), so
resolution fetches that exact message directly by id instead of listing
recent messages and guessing by sender + closest timestamp — the old
guessing approach could silently attach the wrong file when the actual
message had no real attachment.
"""
from datetime import datetime
from types import SimpleNamespace

from app.teams import attachments as attachments_module


def _channel_activity_with_html_only_attachment():
    return SimpleNamespace(
        id="activity-1",
        text="check this file",
        timestamp=None,
        service_url="https://smba.trafficmanager.net/amer/",
        conversation=SimpleNamespace(id="19:abc@thread.tacv2;messageid=123", conversation_type="channel"),
        from_=SimpleNamespace(id="user-1", aad_object_id="aad-user-1"),
        team=SimpleNamespace(id="team-1", aad_group_id="team-1"),
        channel=SimpleNamespace(id="channel-1"),
        attachments=[
            SimpleNamespace(content_type="text/html", content_url=None, content="<div>check this file</div>", name=None),
        ],
    )


def _ctx_for(activity):
    return SimpleNamespace(activity=activity)


def test_channel_html_only_attachment_falls_through_to_graph(monkeypatch):
    """The Bot Framework attachment alone (text/html, no content_url) must trigger the Graph fallback."""
    called = {"fetch": False}

    def fake_fetch(activity, scope):
        called["fetch"] = True
        assert scope == "channel"
        return [{"contentType": "image/png", "contentUrl": "https://contoso.sharepoint.com/sites/x/img.png", "name": "photo.png"}]

    monkeypatch.setattr(attachments_module, "_fetch_graph_message_attachments", fake_fetch)
    monkeypatch.setattr(attachments_module, "_download_graph_share", lambda url: b"fake-image-bytes")

    result = attachments_module.download_attachments(_ctx_for(_channel_activity_with_html_only_attachment()))

    assert called["fetch"] is True
    assert result == [("photo.png", b"fake-image-bytes", "application/octet-stream")]


def test_graph_attachment_without_reference_content_type_is_still_downloaded(monkeypatch):
    """
    Regression test: previously the fallback only accepted
    contentType == "reference" (existing SharePoint/OneDrive shares). A
    freshly-uploaded channel image/file may carry a different Graph
    contentType (e.g. its real MIME type) while still being the correct,
    only available attachment reference — it must not be silently dropped.
    """
    monkeypatch.setattr(
        attachments_module, "_fetch_graph_message_attachments",
        lambda activity, scope: [{"contentType": "image/jpeg", "contentUrl": "https://contoso.sharepoint.com/sites/x/photo.jpg", "name": "photo.jpg"}],
    )
    monkeypatch.setattr(attachments_module, "_download_graph_share", lambda url: b"jpeg-bytes")

    result = attachments_module.download_attachments(_ctx_for(_channel_activity_with_html_only_attachment()))

    assert result == [("photo.jpg", b"jpeg-bytes", "application/octet-stream")]


def test_graph_attachment_without_content_url_is_skipped(monkeypatch):
    """An attachment reference with no contentUrl at all is nothing the Shares API can resolve — must be skipped, not error."""
    monkeypatch.setattr(
        attachments_module, "_fetch_graph_message_attachments",
        lambda activity, scope: [{"contentType": "reference", "contentUrl": None, "name": "no-url.txt"}],
    )

    result = attachments_module.download_attachments(_ctx_for(_channel_activity_with_html_only_attachment()))

    assert result == []


def test_fetch_graph_message_attachments_is_scope_gated():
    """
    Personal (1:1) chats already get real file data straight from Bot
    Framework — _fetch_graph_message_attachments() must return [] for
    "personal" scope without attempting any Graph call at all (no
    team_id/channel_id/conv_id lookups that could themselves raise).
    """
    activity = SimpleNamespace(
        conversation=SimpleNamespace(id="a:conv1", conversation_type="personal"),
        from_=SimpleNamespace(id="user-1", aad_object_id="aad-user-1"),
        timestamp=None,
    )

    assert attachments_module._fetch_graph_message_attachments(activity, "personal") == []


# ── Channel and group chat: direct get-by-id (no listing, no fuzzy match) ──

class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def test_channel_reply_attachment_resolved_by_direct_id_lookup(monkeypatch):
    """
    Regression test for the id-equivalence fix: a REPLY's activity.id (not
    just a thread's root post) equals its own Graph chatMessage.id —
    confirmed live against a real deployment. Resolution must hit exactly
    .../replies/{activity.id} directly, with no listing involved.
    """
    requested_urls: list = []

    def fake_get(url: str, **kwargs):
        requested_urls.append(url)
        assert url.endswith("/replies/reply-42")
        return _FakeResponse(200, {
            "id": "reply-42", "createdDateTime": "2026-01-01T00:10:00Z",
            "from": {"user": {"id": "aad-user-1"}},
            "attachments": [{"contentType": "reference", "contentUrl": "https://contoso.sharepoint.com/x.png", "name": "x.png"}],
        })

    monkeypatch.setattr(attachments_module, "graph_client", type("_G", (), {"get": staticmethod(fake_get)})())
    monkeypatch.setattr(attachments_module, "get_team_id", lambda activity: "team-1")
    monkeypatch.setattr(attachments_module, "get_channel_id", lambda activity: "channel-1")
    monkeypatch.setattr(attachments_module, "get_team_post_id", lambda activity: "thread-1")

    activity = SimpleNamespace(
        id="reply-42",
        conversation=SimpleNamespace(id="19:abc@thread.tacv2;messageid=thread-1", conversation_type="channel"),
        from_=SimpleNamespace(id="user-1", aad_object_id="aad-user-1"),
        timestamp=datetime.fromisoformat("2026-01-01T00:10:00+00:00"),
    )

    result = attachments_module._fetch_graph_message_attachments(activity, "channel")

    assert requested_urls, "no Graph call made"
    assert len(result) == 1
    assert result[0]["name"] == "x.png"


def test_channel_message_with_no_real_attachment_returns_empty_not_a_stale_match(monkeypatch):
    """
    Regression test for the false-match bug the old list-and-fuzzy-match
    approach had: a channel message with NO real attachment (Bot
    Framework's synthetic text/html rendering is present regardless of
    whether a file was attached, so its mere presence used to trigger this
    fallback and, when the actual message had no attachments, silently
    match some unrelated OLDER message that happened to have one) must
    resolve to [] — confirmed live this exact scenario used to return a
    stale wrong file. Direct get-by-id fetches only the one exact message,
    so there's nothing else it could accidentally match.
    """
    def fake_get(url: str, **kwargs):
        assert url.endswith("/replies/reply-1")
        return _FakeResponse(200, {
            "id": "reply-1", "createdDateTime": "2026-01-01T00:05:00Z",
            "from": {"user": {"id": "aad-user-1"}},
            "attachments": [],
        })

    monkeypatch.setattr(attachments_module, "graph_client", type("_G", (), {"get": staticmethod(fake_get)})())
    monkeypatch.setattr(attachments_module, "get_team_id", lambda activity: "team-1")
    monkeypatch.setattr(attachments_module, "get_channel_id", lambda activity: "channel-1")
    monkeypatch.setattr(attachments_module, "get_team_post_id", lambda activity: "thread-1")

    activity = SimpleNamespace(
        id="reply-1",
        conversation=SimpleNamespace(id="19:abc@thread.tacv2;messageid=thread-1", conversation_type="channel"),
        from_=SimpleNamespace(id="user-1", aad_object_id="aad-user-1"),
        timestamp=datetime.fromisoformat("2026-01-01T00:05:00+00:00"),
    )

    result = attachments_module._fetch_graph_message_attachments(activity, "channel")

    assert result == []


def test_group_chat_attachment_resolved_by_direct_id_lookup(monkeypatch):
    """
    Regression test for the id-equivalence fix in group chat: confirmed
    live (against a real deployment) that a group chat message's
    activity.id equals its own Graph chatMessage.id too, just like channel
    — so resolution is a single direct GET .../chats/{chat_id}/messages/{id},
    never a listing or a sender/timestamp guess.
    """
    requested_urls: list = []

    def fake_get(url: str, **kwargs):
        requested_urls.append(url)
        assert url.endswith("/chats/19:abc@thread.v2/messages/msg-7")
        return _FakeResponse(200, {
            "id": "msg-7", "createdDateTime": "2026-01-01T00:10:00Z",
            "from": {"user": {"id": "aad-user-1"}},
            "attachments": [{"contentType": "reference", "contentUrl": "https://contoso-my.sharepoint.com/personal/x/outline.png", "name": "outline.png"}],
        })

    monkeypatch.setattr(attachments_module, "graph_client", type("_G", (), {"get": staticmethod(fake_get)})())

    activity = SimpleNamespace(
        id="msg-7",
        conversation=SimpleNamespace(id="19:abc@thread.v2", conversation_type="groupChat"),
        from_=SimpleNamespace(id="user-1", aad_object_id="aad-user-1"),
        timestamp=datetime.fromisoformat("2026-01-01T00:10:00+00:00"),
    )

    result = attachments_module._fetch_graph_message_attachments(activity, "groupChat")

    assert requested_urls, "no Graph call made"
    assert len(result) == 1
    assert result[0]["name"] == "outline.png"


def test_attachment_resolution_works_for_a_brand_new_root_post(monkeypatch):
    """
    Regression test: a channel thread's own OPENING post has no
    ";messageid=" suffix on its own conversation.id at all (only a reply's
    conversation.id carries one) — get_thread_root_id() alone returns "" for
    it. Without get_team_post_id()'s root-post fallback (its own activity.id
    IS the thread root id), attachment resolution silently bailed out before
    ever calling Graph for the very first message of any new channel thread
    — confirmed live against a real deployment.

    get_team_post_id() itself is NOT mocked here — this test exercises its
    real fallback logic, not just that _fetch_graph_message_attachments()
    calls some function named get_team_post_id.
    """
    requested_urls: list = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        if "/replies" not in url:
            return _FakeResponse(200, {
                "id": "999", "createdDateTime": "2026-01-01T00:00:00Z",
                "from": {"user": {"id": "aad-user-1"}},
                "attachments": [{"contentType": "reference", "contentUrl": "https://contoso.sharepoint.com/manifest.zip", "name": "manifest.zip"}],
            })
        return _FakeResponse(200, {"value": [], "@odata.nextLink": None})

    monkeypatch.setattr(attachments_module, "graph_client", type("_G", (), {"get": staticmethod(fake_get)})())
    monkeypatch.setattr(attachments_module, "get_team_id", lambda activity: "team-1")
    monkeypatch.setattr(attachments_module, "get_channel_id", lambda activity: "channel-1")

    # A brand-new root post: conversation.id has NO ";messageid=" suffix.
    activity = SimpleNamespace(
        id="999",
        conversation=SimpleNamespace(id="19:abc@thread.tacv2", conversation_type="channel"),
        from_=SimpleNamespace(id="user-1", aad_object_id="aad-user-1"),
        timestamp=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
    )

    result = attachments_module._fetch_graph_message_attachments(activity, "channel")

    assert requested_urls, "bailed out before any Graph call — root-post thread id fallback isn't working"
    assert len(result) == 1
    assert result[0]["name"] == "manifest.zip"


def test_download_attachments_counts_only_file_attachments(monkeypatch, caplog):
    """
    When an activity has 1 file attachment and 1 non-file structural attachment (e.g. text/html),
    the logger must count only real file attachments (1 of 1), not raw attachments (1 of 2).
    """
    import logging

    activity = SimpleNamespace(
        id="act-1",
        conversation=SimpleNamespace(id="conv-1", conversation_type="personal"),
        attachments=[
            SimpleNamespace(content_type="text/html", name=None, content_url=None, content="<div>query</div>"),
            SimpleNamespace(
                content_type="application/vnd.microsoft.teams.file.download.info",
                name="sample.pdf",
                content={"downloadUrl": "https://download.example.com/sample.pdf"},
                content_url=None,
            ),
        ],
    )
    monkeypatch.setattr(attachments_module, "_download", lambda url, headers=None: b"fake-pdf-bytes")

    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        results = attachments_module.download_attachments(SimpleNamespace(activity=activity))

    assert len(results) == 1
    assert results[0][0] == "sample.pdf"
    assert "[ATTACHMENT] Downloaded 1 of 1 attachment(s)" in caplog.text
    assert "[ATTACHMENT] Downloaded 1 of 2 attachment(s)" not in caplog.text

