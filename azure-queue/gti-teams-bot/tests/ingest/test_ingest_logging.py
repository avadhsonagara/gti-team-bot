"""
Tests verifying that the ingest function app captures and logs the user query,
user name, scope, and sets observability context fields for Application Insights.
"""
import logging
import time
from unittest.mock import MagicMock

import pytest

from app.observability import RequestContextFilter, _request_ctx, bind_request, clear_request
from function_app import _ingest_message


def _raw_activity(text: str = "what is 1.1.1.1?", sender_name: str | None = None, scope: str = "personal") -> dict:
    from_obj = {"id": "user-456"}
    if sender_name:
        from_obj["name"] = sender_name

    return {
        "type": "message",
        "id": "activity-789",
        "text": text,
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": "conv-456", "conversationType": scope},
        "from": from_obj,
    }


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_request()
    yield
    clear_request()


def test_ingest_message_logs_user_query_prominently(monkeypatch, caplog):
    """Verify that _ingest_message logs the user query upfront at INGEST 1/3."""
    mock_sent = MagicMock()
    mock_sent.id = "placeholder-123"
    monkeypatch.setattr("function_app.Ctx.send", lambda self, text: mock_sent)

    mock_queue = MagicMock()
    monkeypatch.setattr("function_app._get_queue_client", lambda: mock_queue)

    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        _ingest_message(_raw_activity(text="what is 1.1.1.1?", sender_name="Alice Bob", scope="personal"), time.perf_counter())

    log_text = caplog.text
    assert "[INGEST 1/3] Inbound User Query" in log_text
    assert "user='Alice Bob' (user-456)" in log_text
    assert "scope=personal" in log_text
    assert "query='what is 1.1.1.1?'" in log_text
    assert "[INGEST 2/3] Placeholder posted" in log_text
    assert "[INGEST 3/3] Enqueued to" in log_text
    assert "[INGEST DONE] Handoff completed" in log_text
    assert mock_queue.send_message.called


def test_ingest_empty_query_logging(monkeypatch, caplog):
    """Verify logging for empty or whitespace query text."""
    monkeypatch.setattr("function_app.Ctx.send", lambda self, text: None)

    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        _ingest_message(_raw_activity(text="   ", sender_name="Charlie"), time.perf_counter())

    log_text = caplog.text
    assert "[INGEST] Empty query received" in log_text
    assert "user='Charlie' (user-456)" in log_text
