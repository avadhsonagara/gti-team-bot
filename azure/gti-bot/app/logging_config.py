"""
Logging setup for Azure Functions and local console compatibility.

Azure Functions forwards everything written through the standard `logging`
module to Application Insights automatically, so structured formatting isn't
required for ingestion — plain readable text is used unconditionally.
"""
import logging

from app.observability import RequestContextFilter

_LIBRARY_TAG_PREFIXES = (
    ("gti-teams-bot", ""),
    ("microsoft_teams", "[TEAMS]"),
    ("httpx", "[HTTP]"),
    ("httpcore", "[HTTP]"),
    ("azure.storage", "[STORAGE]"),
    ("azure.core", "[AZURE]"),
    ("azure.identity", "[AUTH]"),
)


def _tag_for_logger(name: str) -> str:
    """Bracket tag for a logger name, e.g. 'httpx' -> '[HTTP]'."""
    for prefix, tag in _LIBRARY_TAG_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return tag
    return "[LIB]"


class LibraryTagFilter(logging.Filter):
    """Attaches lib_tag (e.g. '[HTTP] ') to every record for formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        tag = _tag_for_logger(record.name)
        record.lib_tag = f"{tag} " if tag else ""
        return True


class SuppressLibraryTracebacksFilter(logging.Filter):
    """Strips unnecessary tracebacks from third-party libraries."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("gti-teams-bot") and record.exc_info:
            record.exc_info = None
            record.exc_text = None
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
    stream_handler.addFilter(SuppressLibraryTracebacksFilter())

    logging.basicConfig(level=logging.INFO, handlers=[stream_handler], force=True)
