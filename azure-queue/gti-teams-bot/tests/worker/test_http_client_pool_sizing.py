"""
Regression test: GTIAgenticClient and GraphClient must size their connection
pools to settings.concurrent_requests (bicep's workerConcurrentRequests,
which also drives PYTHON_THREADPOOL_THREAD_COUNT), instead of accepting
urllib3's default pool size of 10 regardless of actual thread concurrency —
see bot_client.py's own version of this same fix and app/config.py's comment
on concurrent_requests.
"""
from app.config import settings
from app.gti.client import GTIAgenticClient
from app.graph.client import GraphClient


def test_gti_client_session_pool_matches_worker_concurrency():
    client = GTIAgenticClient(api_key="fake-key")
    session = client._get_session()
    adapter = session.get_adapter("https://www.virustotal.com/")
    assert adapter._pool_connections == settings.concurrent_requests
    assert adapter._pool_maxsize == settings.concurrent_requests


def test_graph_client_session_pool_matches_worker_concurrency():
    client = GraphClient(managed_identity_client_id="fake-client-id")
    session = client._get_session()
    adapter = session.get_adapter("https://graph.microsoft.com/")
    assert adapter._pool_connections == settings.concurrent_requests
    assert adapter._pool_maxsize == settings.concurrent_requests
