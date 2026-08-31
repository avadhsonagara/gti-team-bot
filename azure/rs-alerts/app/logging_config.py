"""
Logging setup for RS Alerts.

Azure Functions forwards everything written through the standard `logging`
module to Application Insights automatically (via APPLICATIONINSIGHTS_CONNECTION_STRING
in host.json / app settings) — no custom exporter needed here, just readable
formatting for the console/log stream.
"""
import logging
import os


def setup_logging() -> None:
    """Configure root logging for local or Azure Functions execution."""
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [rs-alerts] %(message)s",
        datefmt="%H:%M:%S",
    ))

    logging.basicConfig(level=log_level, handlers=[handler], force=True)
