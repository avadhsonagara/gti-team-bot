"""
Regression tests: GTIAgenticClient and GraphClient must size their
connection pools to settings.concurrent_requests (reused from the THREADS
env var terraform sets from var.concurrency), instead of accepting
urllib3's default pool size of 10 regardless of actual concurrency.
"""
from app.config import settings
from app.gti.client import GTIAgenticClient
from app.graph.client import GraphClient


def test_gti_client_session_pool_matches_concurrent_requests():
    client = GTIAgenticClient(api_key="fake-key")
    session = client._get_session()
    adapter = session.get_adapter("https://www.virustotal.com/")
    assert adapter._pool_connections == settings.concurrent_requests
    assert adapter._pool_maxsize == settings.concurrent_requests


def test_graph_client_session_pool_matches_concurrent_requests():
    client = GraphClient(client_id="a", client_secret="b", tenant_id="c")
    session = client._get_session()
    adapter = session.get_adapter("https://graph.microsoft.com/")
    assert adapter._pool_connections == settings.concurrent_requests
    assert adapter._pool_maxsize == settings.concurrent_requests


def test_graph_client_session_creation_is_idempotent_under_repeat_calls():
    """Confirms the double-checked-locking session init returns the same object, not a fresh one each time."""
    client = GraphClient(client_id="a", client_secret="b", tenant_id="c")
    assert client._get_session() is client._get_session()
