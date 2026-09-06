"""
Logging setup for GCP Cloud Logging and local console compatibility.

- On GCP (K_SERVICE / FUNCTION_TARGET set): emits one JSON object per line.
- Locally: emits human-readable formatted lines.
"""
import json
import logging
import os

from app.observability import RequestContextFilter

_LIBRARY_TAG_PREFIXES = (
    ("gti-teams-bot", ""),
    ("microsoft_teams", "[TEAMS]"),
    ("httpx", "[HTTP]"),
    ("httpcore", "[HTTP]"),
    ("google.auth", "[AUTH]"),
    ("google.api_core", "[GCP]"),
    ("google.cloud", "[GCP]"),
    ("uvicorn", "[HTTP]"),
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


_GCP_SEVERITY = {
    logging.DEBUG:    "DEBUG",
    logging.INFO:     "INFO",
    logging.WARNING:  "WARNING",
    logging.ERROR:    "ERROR",
    logging.CRITICAL: "CRITICAL",
}

_RESERVED_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime", "lib_tag",
})


class GCPJsonFormatter(logging.Formatter):
    """One JSON object per log line — compatible with GCP Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        lib_tag = getattr(record, "lib_tag", "")
        entry = {
            "severity": _GCP_SEVERITY.get(record.levelno, "DEFAULT"),
            "message":  f"{lib_tag}{record.getMessage()}",
            "logger":   record.name,
            "module":   record.module,
            "function": record.funcName,
            "line":     record.lineno,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS:
                continue
            if key == "trace":
                entry["logging.googleapis.com/trace"] = value
            elif key not in entry:
                entry[key] = value

        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configure root logging for local or container execution."""
    is_gcp = bool(os.getenv("K_SERVICE") or os.getenv("FUNCTION_TARGET"))

    stream_handler = logging.StreamHandler()
    if is_gcp:
        stream_handler.setFormatter(GCPJsonFormatter())
    else:
        stream_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(request_id)s] | %(lib_tag)s%(message)s",
            datefmt="%H:%M:%S",
        ))

    stream_handler.addFilter(RequestContextFilter())
    stream_handler.addFilter(LibraryTagFilter())
    stream_handler.addFilter(SuppressLibraryTracebacksFilter())

    logging.basicConfig(level=logging.INFO, handlers=[stream_handler], force=True)
