"""Unit tests for Teams channel and team/group ID parsing from URLs and strings."""
from app.sender import extract_channel_id, extract_team_id

_FULL_LINK = (
    "https://teams.microsoft.com/l/channel/19%3aabcDEF123%40thread.tacv2/"
    "my-channel?groupId=11111111-2222-3333-4444-555555555555&tenantId=xyz"
)
_BARE_ID = "19:abcDEF123@thread.tacv2"


def test_extract_channel_id_from_full_link():
    """Verify channel ID extraction from an encoded Teams deep link."""
    assert extract_channel_id(_FULL_LINK) == "19:abcDEF123@thread.tacv2"


def test_extract_channel_id_from_bare_id():
    """Verify channel ID extraction when given a raw thread ID."""
    assert extract_channel_id(_BARE_ID) == "19:abcDEF123@thread.tacv2"


def test_extract_channel_id_unrecognized_format_returns_input_stripped():
    """Verify unformatted channel string returns stripped original value."""
    assert extract_channel_id("  not-a-teams-id  ") == "not-a-teams-id"


def test_extract_team_id_present_in_full_link():
    """Verify group ID extraction from groupId parameter in full link."""
    assert extract_team_id(_FULL_LINK) == "11111111-2222-3333-4444-555555555555"


def test_extract_team_id_absent_from_bare_channel_id():
    """Verify team ID extraction returns None when given a bare channel ID."""
    assert extract_team_id(_BARE_ID) is None

