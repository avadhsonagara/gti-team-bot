"""
Automates the five is_placeholder_message() scenarios the README describes
as having been manually verified — locking in that behavior with a real
regression test instead of a one-time manual check.
"""
from app.constants import PLACEHOLDER_TEXT
from app.teams.thread import is_placeholder_message

_BOT_APP_ID = "11111111-1111-1111-1111-111111111111"
_OTHER_APP_ID = "22222222-2222-2222-2222-222222222222"


def _msg(body_content: str, from_application_id: str | None = None, from_user_id: str | None = "user-1") -> dict:
    frm: dict = {}
    if from_application_id is not None:
        frm["application"] = {"id": from_application_id}
    if from_user_id is not None:
        frm["user"] = {"id": from_user_id}
    return {"from": frm, "body": {"content": body_content}}


def test_bots_own_placeholder_matched_by_app_id():
    msg = _msg(PLACEHOLDER_TEXT, from_application_id=_BOT_APP_ID, from_user_id=None)
    assert is_placeholder_message(msg, _BOT_APP_ID) is True


def test_bots_own_placeholder_when_graph_omits_application_id():
    """Fallback: no from.application.id at all, but also no from.user — matched by 'no user' + text."""
    msg = _msg(PLACEHOLDER_TEXT, from_application_id=None, from_user_id=None)
    assert is_placeholder_message(msg, _BOT_APP_ID) is True


def test_different_bots_message_with_same_text_not_excluded():
    msg = _msg(PLACEHOLDER_TEXT, from_application_id=_OTHER_APP_ID, from_user_id=None)
    assert is_placeholder_message(msg, _BOT_APP_ID) is False


def test_human_quoting_placeholder_text_verbatim_not_excluded():
    msg = _msg(f"> {PLACEHOLDER_TEXT}\nany updates?", from_application_id=None, from_user_id="user-1")
    assert is_placeholder_message(msg, _BOT_APP_ID) is False


def test_ordinary_human_message_not_excluded():
    msg = _msg("what is 1.1.1.1?", from_application_id=None, from_user_id="user-1")
    assert is_placeholder_message(msg, _BOT_APP_ID) is False
