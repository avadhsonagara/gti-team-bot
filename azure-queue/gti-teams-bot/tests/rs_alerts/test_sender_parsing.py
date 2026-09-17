"""Regression tests for parsing a Teams channel link/ID into the canonical channel ID and, when present, the team's group ID (used for Teams-app auto-install)."""
from app.sender import extract_channel_id, extract_team_id

_FULL_LINK = (
    "https://teams.microsoft.com/l/channel/19%3aabcDEF123%40thread.tacv2/"
    "my-channel?groupId=11111111-2222-3333-4444-555555555555&tenantId=xyz"
)
_BARE_ID = "19:abcDEF123@thread.tacv2"


def test_extract_channel_id_from_full_link():
    assert extract_channel_id(_FULL_LINK) == "19:abcDEF123@thread.tacv2"


def test_extract_channel_id_from_bare_id():
    assert extract_channel_id(_BARE_ID) == "19:abcDEF123@thread.tacv2"


def test_extract_channel_id_unrecognized_format_returns_input_stripped():
    assert extract_channel_id("  not-a-teams-id  ") == "not-a-teams-id"


def test_extract_team_id_present_in_full_link():
    assert extract_team_id(_FULL_LINK) == "11111111-2222-3333-4444-555555555555"


def test_extract_team_id_absent_from_bare_channel_id():
    """A bare channel ID carries no groupId — auto-install must be skippable, not crash."""
    assert extract_team_id(_BARE_ID) is None
