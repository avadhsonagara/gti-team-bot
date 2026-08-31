"""
Logging setup for RS Alerts.

Azure Functions forwards everything written through the standard `logging`
module to Application Insights automatically (via APPLICATIONINSIGHTS_CONNECTION_STRING
in host.json / app settings) — no custom exporter needed for ingestion. Text
is the default; set LOG_FORMAT=json for line-delimited JSON instead (e.g. for
local structured-log tooling or Log Analytics queries over the raw stream).
"""
import json
import logging
import os

_RESERVED_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
})


class JsonFormatter(logging.Formatter):
    """One JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and key not in entry:
                entry[key] = value

        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configure root logging for local or Azure Functions execution."""
    log_format = os.getenv("LOG_FORMAT", "text").lower()
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [rs-alerts] %(message)s",
            datefmt="%H:%M:%S",
        ))

    logging.basicConfig(level=log_level, handlers=[handler], force=True)
