"""
Tests for app/poison_handler.py — the dead-letter push target that tells the
user their request permanently failed after Pub/Sub exhausts
max_delivery_attempts on the main job subscription.
"""
from app.poison_handler import process_poison_job


def _raw_payload(scope: str = "personal") -> dict:
    return {
        "kind": "message",
        "activity": {
            "type": "message",
            "id": "activity-1",
            "text": "what is 1.1.1.1?",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "conversation": {"id": "conv-1", "conversationType": scope},
            "from": {"id": "user-1"},
        },
        "loadingActivityId": "placeholder-1",
        "enqueuedAt": "2024-01-01T00:00:00Z",
    }


def test_delivers_permanent_failure_notice(monkeypatch):
    delivered_calls = []
    monkeypatch.setattr(
        "app.poison_handler.deliver_message",
        lambda ctx, loading_activity_id, text, card, edit_in_place=False: delivered_calls.append((text, edit_in_place)) or True,
    )

    process_poison_job(_raw_payload())

    assert len(delivered_calls) == 1
    text, edit_in_place = delivered_calls[0]
    assert "Unable to Complete Request" in text
    assert edit_in_place is False  # personal chat -> delete-and-repost, not edit


def test_channel_scope_edits_in_place(monkeypatch):
    delivered_calls = []
    monkeypatch.setattr(
        "app.poison_handler.deliver_message",
        lambda ctx, loading_activity_id, text, card, edit_in_place=False: delivered_calls.append(edit_in_place) or True,
    )

    process_poison_job(_raw_payload(scope="channel"))

    assert delivered_calls == [True]


def test_installation_update_remove_job_is_skipped_without_delivery(monkeypatch):
    delivered_calls = []
    monkeypatch.setattr(
        "app.poison_handler.deliver_message",
        lambda *a, **kw: delivered_calls.append(1) or True,
    )

    process_poison_job({"kind": "installationUpdateRemove", "activity": {}})

    assert delivered_calls == [], "There is no placeholder or conversation to notify for a cleanup job."


def test_malformed_payload_does_not_raise(monkeypatch):
    delivered_calls = []
    monkeypatch.setattr(
        "app.poison_handler.deliver_message",
        lambda *a, **kw: delivered_calls.append(1) or True,
    )

    process_poison_job({"kind": "message", "loadingActivityId": "x"})  # missing 'activity'

    assert delivered_calls == []
