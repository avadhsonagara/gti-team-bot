"""
Tests verifying that the worker function app captures and logs the user query,
user name, scope, and sets observability context fields for Application Insights.
"""
import logging
from datetime import datetime, timezone

import pytest

from app.job_processor import process_job
from app.observability import RequestContextFilter, _request_ctx, bind_request, clear_request


def _raw_payload(text: str = "check 8.8.8.8", sender_name: str | None = None, scope: str = "personal") -> dict:
    from_obj = {"id": "user-123"}
    if sender_name:
        from_obj["name"] = sender_name

    return {
        "activity": {
            "type": "message",
            "id": "activity-abc",
            "text": text,
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "conversation": {"id": "conv-xyz", "conversationType": scope},
            "from": from_obj,
        },
        "loadingActivityId": "placeholder-abc",
        "enqueuedAt": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_request()
    yield
    clear_request()


@pytest.fixture(autouse=True)
def _mock_dependencies(monkeypatch):
    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: True)
    monkeypatch.setattr(
        "app.job_processor.gti_client.send_message",
        lambda **kwargs: ("session-123", "GTI Report: 8.8.8.8 is benign.", None),
    )


def test_request_context_filter_injects_query_user_name_and_scope():
    """Verify that RequestContextFilter copies query, user_name, and scope to LogRecord."""
    bind_request(
        request_id="req-1",
        user="u-1",
        user_name="John Doe",
        query="what is 1.1.1.1?",
        scope="channel",
    )

    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test message",
        args=(),
        exc_info=None,
    )
    flt = RequestContextFilter()
    flt.filter(record)

    assert getattr(record, "request_id", None) == "req-1"
    assert getattr(record, "user", None) == "u-1"
    assert getattr(record, "user_name", None) == "John Doe"
    assert getattr(record, "query", None) == "what is 1.1.1.1?"
    assert getattr(record, "scope", None) == "channel"


def test_process_job_logs_user_query_prominently(caplog):
    """Verify that process_job logs the user query upfront at WORKER START."""
    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        process_job(_raw_payload(text="analyze malicious.exe", sender_name="Jane Doe", scope="personal"))

    log_text = caplog.text
    assert "[WORKER START] Processing User Query" in log_text
    assert "user='Jane Doe' (user-123)" in log_text
    assert "scope=personal" in log_text
    assert "query='analyze malicious.exe'" in log_text
    assert "[WORKER DONE] Job finished successfully" in log_text
    assert "status=delivered" in log_text


def test_process_job_falls_back_to_user_id_when_name_missing(caplog):
    """Verify fallback when sender name is omitted from activity."""
    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        process_job(_raw_payload(text="lookup domain.com", sender_name=None))

    log_text = caplog.text
    assert "[WORKER START] Processing User Query" in log_text
    assert "user='user-123' (user-123)" in log_text
    assert "query='lookup domain.com'" in log_text


def test_process_job_logs_worker_retry_on_subsequent_dequeue(caplog):
    """Verify that process_job logs [WORKER RETRY] when dequeue_count > 1."""
    with caplog.at_level(logging.WARNING, logger="gti-teams-bot"):
        process_job(_raw_payload(text="retry query", sender_name="Bob"), dequeue_count=2)

    log_text = caplog.text
    assert "[WORKER RETRY] Retrying job execution (attempt 2/2)" in log_text
    assert "user='Bob' (user-123)" in log_text
    assert "query='retry query'" in log_text


def test_process_job_logs_retry_scheduled_on_delivery_failure(monkeypatch, caplog):
    """Verify that process_job logs [WORKER RETRY SCHEDULED] when delivery fails."""
    from app.job_processor import DeliveryFailedError

    monkeypatch.setattr("app.job_processor.deliver_message", lambda *a, **kw: False)

    with caplog.at_level(logging.WARNING, logger="gti-teams-bot"):
        with pytest.raises(DeliveryFailedError):
            process_job(_raw_payload(text="failing query", sender_name="Carol"), dequeue_count=1)

    log_text = caplog.text
    assert "[WORKER RETRY SCHEDULED] Delivery failed (attempt 1/2)" in log_text
    assert "user='Carol'" in log_text
    assert "query='failing query'" in log_text

