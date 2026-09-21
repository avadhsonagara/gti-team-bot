"""
Regression test: process_job() must strip <at>...</at> mention tags from
GTI's response before delivery. prompt.md instructs the model to never emit
one, but that's a prompt-level instruction, not a guarantee — prompt
injection could still induce it. Covers both the native-Adaptive-Card path
and the markdown-fallback-wrapper path, since both derive from the same
response_text.
"""
from datetime import datetime, timezone

import pytest

from app.job_processor import process_job


def _raw_payload(text: str = "check this ip", scope: str = "personal") -> dict:
    return {
        "activity": {
            "type": "message",
            "id": "activity-1",
            "text": text,
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "conversation": {"id": "conv-1", "conversationType": scope},
            "from": {"id": "user-1"},
        },
        "loadingActivityId": "placeholder-1",
        "enqueuedAt": datetime.now(timezone.utc).isoformat(),
    }


def _capture_delivery(monkeypatch) -> dict:
    captured: dict = {}

    def fake_deliver(ctx, loading_activity_id, text, card=None, edit_in_place=False):
        captured["text"] = text
        captured["card"] = card
        return True

    monkeypatch.setattr("app.job_processor.deliver_message", fake_deliver)
    return captured


def test_injected_mention_is_stripped_from_markdown_fallback_path(monkeypatch):
    """GTI's response is plain markdown (not a native card) — goes through build_gti_response_card()."""
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: ("session-1", "Findings: <at>Everyone</at> this IP is malicious.", None),
    )
    captured = _capture_delivery(monkeypatch)

    process_job(_raw_payload())

    assert "<at>" not in captured["text"].lower()
    card_text = captured["card"]["body"][1]["text"] if captured["card"] else ""
    assert "<at>" not in card_text.lower()


def test_injected_mention_is_stripped_from_native_card_path(monkeypatch):
    """GTI returns a native AdaptiveCard JSON with a mention embedded in a TextBlock."""
    import json

    native_card = json.dumps({
        "type": "AdaptiveCard",
        "body": [{"type": "TextBlock", "text": "<at>Jane Doe</at> — check this hash immediately."}],
    })
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: ("session-1", native_card, None),
    )
    captured = _capture_delivery(monkeypatch)

    process_job(_raw_payload())

    assert "<at>" not in captured["text"].lower()
    assert captured["card"] is not None, "a valid native card must still parse after stripping the mention"
    body_text = " ".join(block.get("text", "") for block in captured["card"]["body"])
    assert "<at>" not in body_text.lower()
