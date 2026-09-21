"""Unit tests for Teams channel ID parsing from URLs and strings."""
from app.sender import extract_channel_id

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
