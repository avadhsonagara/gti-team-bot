"""
Regression test for finding #5: bot_client.py's shared requests.Session must
retry transient Bot Framework Connector failures (429/5xx) — but must NOT
retry POST (send_activity's method), since retrying a POST that may have
already been processed server-side risks double-posting a message to the
user. PUT/DELETE (update_activity/delete_activity) are idempotent and should
be retried.
"""
from app.teams import bot_client


def test_retry_adapter_is_mounted_with_expected_status_codes():
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    retry = adapter.max_retries
    assert retry.total == 3
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}


def test_post_is_not_in_the_retried_methods():
    """send_activity() creates a new message via POST — must not be blindly retried."""
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert "POST" not in adapter.max_retries.allowed_methods


def test_put_and_delete_are_in_the_retried_methods():
    """update_activity() (PUT) and delete_activity() (DELETE) are idempotent — safe to retry."""
    adapter = bot_client._session.get_adapter("https://smba.trafficmanager.net/")
    assert "PUT" in adapter.max_retries.allowed_methods
    assert "DELETE" in adapter.max_retries.allowed_methods
