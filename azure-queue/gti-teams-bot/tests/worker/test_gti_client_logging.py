"""
Tests verifying GTI client request attempt logging:
- Initial attempt logs "[GTI] METHOD /endpoint" without "(attempt 1/4)".
- Subsequent retries log "[GTI] METHOD /endpoint (attempt 1/3)" etc.
"""
import logging
from unittest.mock import MagicMock

from app.gti.client import GTIAgenticClient


def test_gti_client_logging_initial_attempt_omits_counter(monkeypatch, caplog):
    """Initial GTI request must log without (attempt 1/4)."""
    client = GTIAgenticClient(api_key="test-api-key", max_retries=3, retry_delay=0.01)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"id": "session-xyz"}

    mock_session = MagicMock()
    mock_session.request.return_value = fake_resp
    monkeypatch.setattr(client, "_get_session", lambda: mock_session)

    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        res = client._send_request_with_retries("POST", "/agentspace/sessions")

    assert res == {"id": "session-xyz"}
    log_text = caplog.text
    assert "[GTI] POST /agentspace/sessions" in log_text
    assert "(attempt 1/4)" not in log_text
    assert "(attempt" not in log_text


def test_gti_client_logging_retries_show_attempt_counter(monkeypatch, caplog):
    """First retry must log as (attempt 1/3) when max_retries=3."""
    client = GTIAgenticClient(api_key="test-api-key", max_retries=3, retry_delay=0.01)

    fake_resp_500 = MagicMock()
    fake_resp_500.status_code = 500
    fake_resp_500.text = "Internal Server Error"

    fake_resp_200 = MagicMock()
    fake_resp_200.status_code = 200
    fake_resp_200.json.return_value = {"id": "session-xyz"}

    mock_session = MagicMock()
    mock_session.request.side_effect = [fake_resp_500, fake_resp_200]
    monkeypatch.setattr(client, "_get_session", lambda: mock_session)

    with caplog.at_level(logging.INFO, logger="gti-teams-bot"):
        res = client._send_request_with_retries("POST", "/agentspace/sessions")

    assert res == {"id": "session-xyz"}
    log_text = caplog.text
    # First attempt has no attempt counter
    assert "[GTI] POST /agentspace/sessions\n" in log_text or "[GTI] POST /agentspace/sessions " in log_text
    # Retry attempt shows 1/3
    assert "[GTI] POST /agentspace/sessions (attempt 1/3)" in log_text
    assert "(attempt 1/4)" not in log_text
    assert "(attempt 2/4)" not in log_text
