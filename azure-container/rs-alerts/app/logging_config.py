"""
Logging setup for RS Alerts.

Azure Functions forwards everything written through the standard `logging`
module to Application Insights automatically (via APPLICATIONINSIGHTS_CONNECTION_STRING
in host.json / app settings) — plain readable text is used unconditionally.
"""
import logging


def setup_logging() -> None:
    """Configure root logging for local or Azure Functions execution."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [rs-alerts] %(message)s",
        datefmt="%H:%M:%S",
    ))

    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
