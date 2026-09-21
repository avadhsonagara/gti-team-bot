"""
Regression test: _handle_user_query() must strip <at>...</at> mention tags
from GTI's response before delivery. prompt.md instructs the model to never
emit one, but that's a prompt-level instruction, not a guarantee — prompt
injection could still induce it. Covers both the native-Adaptive-Card path
and the markdown-fallback-wrapper path, since both derive from the same
response_text.

Also confirms GTISessionNotFoundError is no longer imported/caught in
handlers.py — send_message() already swallows it internally and falls back
to creating a new session, so the except block that used to "handle" it was
dead code (send_message can never propagate that exception to its caller).
"""
from types import SimpleNamespace

from app.teams import handlers


def _ctx():
    return SimpleNamespace(
        activity=SimpleNamespace(id="act-1", conversation=SimpleNamespace(id="conv-1", conversation_type="personal")),
        send=lambda text: SimpleNamespace(id="placeholder-1"),
    )


def _patch_common(monkeypatch):
    monkeypatch.setattr(handlers, "download_attachments", lambda ctx: [])
    monkeypatch.setattr(handlers, "get_thread_context", lambda activity, scope: "")
    monkeypatch.setattr(handlers, "get_output_format", lambda settings: "")
    monkeypatch.setattr(handlers, "get_session_id", lambda *a, **kw: None)
    monkeypatch.setattr(handlers, "set_session_id", lambda *a, **kw: None)
    monkeypatch.setattr(handlers, "get_team_id", lambda activity: "")
    monkeypatch.setattr(handlers, "get_channel_id", lambda activity: "")
    monkeypatch.setattr(handlers, "get_session_key", lambda activity, scope: "conv-1")


def _capture_delivery(monkeypatch) -> dict:
    captured: dict = {}

    def fake_deliver(ctx, loading_activity_id, text, card=None, edit_in_place=False):
        captured["text"] = text
        captured["card"] = card
        return True

    monkeypatch.setattr(handlers, "deliver_message", fake_deliver)
    return captured


def test_injected_mention_is_stripped_from_markdown_fallback_path(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        handlers.gti_client, "send_message",
        lambda **kw: ("session-1", "Findings: <at>Everyone</at> this IP is malicious.", {}),
    )
    captured = _capture_delivery(monkeypatch)

    handlers._handle_user_query(_ctx(), "check this ip", "tenant-1", "conv-1", "personal", "user-1", "Alice")

    assert "<at>" not in captured["text"].lower()
    card_text = captured["card"]["body"][1]["text"] if captured["card"] else ""
    assert "<at>" not in card_text.lower()


def test_injected_mention_is_stripped_from_native_card_path(monkeypatch):
    import json

    _patch_common(monkeypatch)
    native_card = json.dumps({
        "type": "AdaptiveCard",
        "body": [{"type": "TextBlock", "text": "<at>Jane Doe</at> — check this hash immediately."}],
    })
    monkeypatch.setattr(handlers.gti_client, "send_message", lambda **kw: ("session-1", native_card, {}))
    captured = _capture_delivery(monkeypatch)

    handlers._handle_user_query(_ctx(), "check this hash", "tenant-1", "conv-1", "personal", "user-1", "Alice")

    assert "<at>" not in captured["text"].lower()
    assert captured["card"] is not None
    body_text = " ".join(block.get("text", "") for block in captured["card"]["body"])
    assert "<at>" not in body_text.lower()


def test_gti_session_not_found_error_is_no_longer_imported_or_caught():
    import inspect

    assert not hasattr(handlers, "GTISessionNotFoundError")
    source = inspect.getsource(handlers)
    assert "except GTISessionNotFoundError" not in source
