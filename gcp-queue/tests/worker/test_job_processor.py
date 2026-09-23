"""
Tests for app/job_processor.py's retry-signal taxonomy: named GTI*Error
subtypes are terminal (deliver a friendly card, return normally), while
DeliveryFailedError, an unexpected Exception, and DuplicateDeliveryError are
the only cases meant to make main.py's /tasks/process return a non-2xx (or,
for the duplicate case, a 200 that skips reprocessing) — see
app/job_processor.py's module docstring and README.md's status-code mapping
table.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.gti.client import GTIRateLimitError
from app.job_processor import DeliveryFailedError, DuplicateDeliveryError, process_job


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


@pytest.fixture(autouse=True)
def _mock_dedup_claim(monkeypatch):
    """Every test in this file gets a fresh (unclaimed) messageId by default."""
    monkeypatch.setattr("app.job_processor.claim_message", lambda message_id: True)


def test_successful_delivery_does_not_raise(monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: True)
    process_job(_raw_payload(), message_id="msg-1")  # must not raise


def test_failed_delivery_raises_delivery_failed_error(monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: False)
    with pytest.raises(DeliveryFailedError):
        process_job(_raw_payload(), message_id="msg-1")


def test_gti_rate_limit_error_delivers_friendly_notice_and_does_not_raise(monkeypatch):
    def _raise_rate_limit(**kwargs):
        raise GTIRateLimitError("rate limited")

    monkeypatch.setattr("app.job_processor.gti_client.send_message", _raise_rate_limit)

    delivered_calls = []
    monkeypatch.setattr(
        "app.job_processor.deliver_message",
        lambda ctx, loading_activity_id, text, card, edit_in_place=False: delivered_calls.append(text) or True,
    )

    process_job(_raw_payload(), message_id="msg-1")  # named GTI* exceptions are terminal — must not raise

    assert len(delivered_calls) == 1
    assert "High Demand" in delivered_calls[0]


def test_unexpected_exception_is_reraised_for_redelivery(monkeypatch):
    """The bare `except Exception: raise` branch — anything unanticipated must still propagate."""
    def _boom(**kwargs):
        raise RuntimeError("something truly unexpected")

    monkeypatch.setattr("app.job_processor.gti_client.send_message", _boom)

    with pytest.raises(RuntimeError):
        process_job(_raw_payload(), message_id="msg-1")


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
    process_job(_raw_payload(enqueued_at=stale_enqueued_at), message_id="msg-1")

    assert calls["gti"] == 0, "A stale job must not trigger an expensive GTI query."
    assert len(delivered_calls) == 1
    assert "Timed Out" in delivered_calls[0]


def test_already_claimed_message_id_raises_duplicate_without_touching_teams(monkeypatch):
    """
    A concurrent/redelivered messageId that another delivery already claimed
    must not re-run the GTI query or touch the placeholder — the delivery
    holding the claim is responsible for that. main.py maps
    DuplicateDeliveryError to a 200 (ack, no reprocessing) rather than 500.
    """
    monkeypatch.setattr("app.job_processor.claim_message", lambda message_id: False)

    gti_calls = {"count": 0}
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: gti_calls.__setitem__("count", gti_calls["count"] + 1) or ("s", "r", None),
    )
    deliver_calls = {"count": 0}
    monkeypatch.setattr(
        "app.job_processor.deliver_message",
        lambda *a, **kw: deliver_calls.__setitem__("count", deliver_calls["count"] + 1) or True,
    )

    with pytest.raises(DuplicateDeliveryError):
        process_job(_raw_payload(), message_id="already-claimed-msg")

    assert gti_calls["count"] == 0
    assert deliver_calls["count"] == 0
