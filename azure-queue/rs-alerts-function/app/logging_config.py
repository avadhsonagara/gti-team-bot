"""
Logging configuration for the RS Alerts Function App.

Sets up standard logging formats for local console execution and Azure Application Insights forwarding.
"""
import logging


def setup_logging() -> None:
    """
    Configure the root logger with a standardized stream handler and formatter.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [rs-alerts] %(message)s",
        datefmt="%H:%M:%S",
    ))

    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

