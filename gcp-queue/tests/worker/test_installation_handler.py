"""
Tests for app/installation_handler.py, including the team_id fallback carried
over from gcp/gcp-bot-function's handle_installation_removed (present there,
but absent from azure-queue's equivalent — see README.md's fix notes):
channelData.team isn't always populated on an installationUpdate activity,
so a missing team_id falls back to session_store's cached channel->team
mapping before giving up.
"""
import pytest

from app.installation_handler import process_installation_removed
from app.queue_job import InvalidJobPayload


def _payload(team: dict | None = None, channel: dict | None = None) -> dict:
    conversation = {"id": "19:abc@thread.tacv2", "conversationType": "channel"}
    activity: dict = {"type": "installationUpdate", "action": "remove", "conversation": conversation}
    if team is not None:
        activity["channelData"] = {"team": team}
    if channel is not None:
        activity.setdefault("channelData", {})["channel"] = channel
    return {"kind": "installationUpdateRemove", "activity": activity}


def test_team_id_resolved_directly_from_activity(monkeypatch):
    deleted = []
    monkeypatch.setattr("app.installation_handler.delete_team_sessions", lambda team_id: deleted.append(team_id))

    process_installation_removed(_payload(team={"aadGroupId": "team-direct"}))

    assert deleted == ["team-direct"]


def test_team_id_falls_back_to_cached_channel_mapping(monkeypatch):
    deleted = []
    monkeypatch.setattr("app.installation_handler.delete_team_sessions", lambda team_id: deleted.append(team_id))
    monkeypatch.setattr(
        "app.installation_handler.get_team_id_for_channel",
        lambda channel_id: "team-from-cache" if channel_id == "channel-1" else None,
    )
    monkeypatch.setattr("app.installation_handler.get_channel_id", lambda activity: "channel-1")
    monkeypatch.setattr("app.installation_handler.get_team_id", lambda activity: "")

    process_installation_removed(_payload())

    assert deleted == ["team-from-cache"]


def test_no_resolvable_team_id_skips_cleanup_without_error(monkeypatch):
    deleted = []
    monkeypatch.setattr("app.installation_handler.delete_team_sessions", lambda team_id: deleted.append(team_id))
    monkeypatch.setattr("app.installation_handler.get_team_id_for_channel", lambda channel_id: None)
    monkeypatch.setattr("app.installation_handler.get_channel_id", lambda activity: "")
    monkeypatch.setattr("app.installation_handler.get_team_id", lambda activity: "")

    process_installation_removed(_payload())

    assert deleted == []


def test_malformed_payload_raises_invalid_job_payload():
    with pytest.raises(InvalidJobPayload):
        process_installation_removed({"kind": "installationUpdateRemove"})
