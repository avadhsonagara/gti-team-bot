"""
Regression test for get_thread_context()'s thread-id resolution fix.

gcp/gcp-bot-function's synchronous thread.py computed
`thread_id = get_thread_root_id(activity.conversation.id)` directly, which
has no fallback for a channel thread's own OPENING post — only replies carry
the ";messageid=" suffix on their conversation.id. get_thread_context()
should use get_session_key() instead (which already has the correct
root-id-or-own-activity-id fallback, used elsewhere for GTI session
continuity), or thread context silently comes back empty for the very first
message of a brand-new channel thread.
"""
from types import SimpleNamespace

from app.teams import thread as thread_module


_ROOT_ID = "1234567890"  # Teams message ids are numeric — the ";messageid=" regex only matches \d+


def _channel_activity_opening_post() -> SimpleNamespace:
    """
    A channel message that is itself the FIRST post of a new thread: its own
    conversation.id carries no ";messageid=" suffix (only a reply's would).
    """
    return SimpleNamespace(
        id=_ROOT_ID,
        timestamp=None,
        conversation=SimpleNamespace(id="19:abc@thread.tacv2", conversation_type="channel"),
        team=SimpleNamespace(aad_group_id="team-1", id="team-1"),
        channel=SimpleNamespace(id="channel-1"),
    )


def _channel_activity_reply() -> SimpleNamespace:
    """A reply within an existing thread: conversation.id DOES carry the suffix."""
    return SimpleNamespace(
        id="9999999999",
        timestamp=None,
        conversation=SimpleNamespace(id=f"19:abc@thread.tacv2;messageid={_ROOT_ID}", conversation_type="channel"),
        team=SimpleNamespace(aad_group_id="team-1", id="team-1"),
        channel=SimpleNamespace(id="channel-1"),
    )


def test_thread_id_falls_back_to_activity_id_for_a_threads_opening_post(monkeypatch):
    monkeypatch.setattr(thread_module.settings, "thread_context_enabled", True)
    captured = {}

    def fake_fetch(team_id, channel_id, thread_id, **kwargs):
        captured["thread_id"] = thread_id
        return []

    monkeypatch.setattr(thread_module, "fetch_thread_messages", fake_fetch)

    thread_module.get_thread_context(_channel_activity_opening_post(), "channel")

    # The buggy version (raw get_thread_root_id(conversation.id)) would have
    # resolved this to "" for a thread's own opening post, and
    # get_thread_context would have short-circuited before ever calling
    # fetch_thread_messages at all.
    assert captured.get("thread_id") == _ROOT_ID


def test_thread_id_uses_the_suffix_for_a_reply(monkeypatch):
    monkeypatch.setattr(thread_module.settings, "thread_context_enabled", True)
    captured = {}

    def fake_fetch(team_id, channel_id, thread_id, **kwargs):
        captured["thread_id"] = thread_id
        return []

    monkeypatch.setattr(thread_module, "fetch_thread_messages", fake_fetch)

    thread_module.get_thread_context(_channel_activity_reply(), "channel")

    assert captured.get("thread_id") == _ROOT_ID


def test_non_channel_scope_never_fetches():
    assert thread_module.get_thread_context(_channel_activity_opening_post(), "personal") == ""
