"""
Logging configuration for Azure Functions and local console execution.

Configures root logging with custom filters for request context injection
and library category tagging.
"""
import logging

from app.observability import RequestContextFilter

_LIBRARY_TAG_PREFIXES = (
    ("gti-teams-bot", ""),
    ("urllib3", "[HTTP]"),
    ("azure.storage", "[STORAGE]"),
    ("azure.core", "[AZURE]"),
    ("azure.identity", "[AUTH]"),
)


def _tag_for_logger(name: str) -> str:
    """
    Return a bracketed category tag for a given logger name.

    Args:
        name: Logger name string (e.g., 'urllib3', 'azure.storage').

    Returns:
        Formatted category prefix tag.
    """
    for prefix, tag in _LIBRARY_TAG_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return tag
    return "[LIB]"


class LibraryTagFilter(logging.Filter):
    """Logging filter that attaches a library category tag to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach lib_tag attribute to the log record for formatting."""
        tag = _tag_for_logger(record.name)
        record.lib_tag = f"{tag} " if tag else ""
        return True


def setup_logging() -> None:
    """Configure root logging for local or Azure Functions execution."""
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(request_id)s] | %(lib_tag)s%(message)s",
        datefmt="%H:%M:%S",
    ))

    stream_handler.addFilter(RequestContextFilter())
    stream_handler.addFilter(LibraryTagFilter())

    logging.basicConfig(level=logging.INFO, handlers=[stream_handler], force=True)
