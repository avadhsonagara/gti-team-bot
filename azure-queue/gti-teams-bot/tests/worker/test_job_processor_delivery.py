"""
Tests for message delivery handling in job processor.

Verifies that delivery failures raise DeliveryFailedError to allow queue retry,
while terminal GTI errors deliver status notifications to the user without raising.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.gti.client import GTIRateLimitError
from app.job_processor import DeliveryFailedError, process_job


def _raw_payload(text: str = "what is 1.1.1.1?", enqueued_at: datetime | None = None, scope: str = "personal") -> dict:
    enqueued_at = enqueued_at or datetime.now(timezone.utc)
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
        "enqueuedAt": enqueued_at.isoformat(),
    }


@pytest.fixture(autouse=True)
def _mock_gti_send_message(monkeypatch):
    """Every test in this file controls GTI's response explicitly; default to a simple success."""
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: ("session-1", "**GTI**: no threats found.", None),
    )


def test_successful_delivery_does_not_raise(monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: True)
    process_job(_raw_payload())  # must not raise


def test_failed_delivery_raises_delivery_failed_error(monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: False)
    with pytest.raises(DeliveryFailedError):
        process_job(_raw_payload())


def test_gti_rate_limit_error_delivers_friendly_notice_and_does_not_raise(monkeypatch):
    def _raise_rate_limit(**kwargs):
        raise GTIRateLimitError("rate limited")

    monkeypatch.setattr("app.job_processor.gti_client.send_message", _raise_rate_limit)

    delivered_calls = []
    monkeypatch.setattr(
        "app.job_processor.deliver_message",
        lambda ctx, loading_activity_id, text, card, edit_in_place=False: delivered_calls.append(text) or True,
    )

    process_job(_raw_payload())  # named GTI* exceptions are terminal — must not raise

    assert len(delivered_calls) == 1
    assert "High Demand" in delivered_calls[0]


def test_stale_job_notifies_without_calling_gti(monkeypatch):
    calls = {"gti": 0}
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: calls.__setitem__("gti", calls["gti"] + 1) or ("s", "r", None),
    )
    delivered_calls = []
    monkeypatch.setattr(
        "app.job_processor.deliver_message",
        lambda ctx, loading_activity_id, text, card, edit_in_place=False: delivered_calls.append(text) or True,
    )

    stale_enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=settings.max_job_age_seconds + 60)
    process_job(_raw_payload(enqueued_at=stale_enqueued_at))

    assert calls["gti"] == 0, "A stale job must not trigger an expensive GTI query."
    assert len(delivered_calls) == 1
    assert "Timed Out" in delivered_calls[0]
