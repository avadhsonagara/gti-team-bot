"""Microsoft Graph integration — generic app-only HTTP client (auth). See app/teams/thread.py for channel thread message fetching."""
from app.graph.client import GraphClient, GraphError, graph_client

__all__ = ["GraphClient", "GraphError", "graph_client"]
