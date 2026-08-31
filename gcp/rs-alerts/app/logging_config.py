"""
Logging setup for RS Alerts on Google Cloud Platform.
"""
import json
import logging
import os

_GCP_SEVERITY = {
    logging.DEBUG:    "DEBUG",
    logging.INFO:     "INFO",
    logging.WARNING:  "WARNING",
    logging.ERROR:    "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class GCPJsonFormatter(logging.Formatter):
    """One JSON object per log line — compatible with GCP Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": _GCP_SEVERITY.get(record.levelno, "DEFAULT"),
            "message":  record.getMessage(),
            "logger":   record.name,
            "module":   record.module,
            "function": record.funcName,
            "line":     record.lineno,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configure root logging for local or Cloud Run execution."""
    is_gcp = bool(os.getenv("K_SERVICE") or os.getenv("FUNCTION_TARGET"))
    log_format = os.getenv("LOG_FORMAT", "json" if is_gcp else "text").lower()
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    stream_handler = logging.StreamHandler()
    if log_format == "json":
        stream_handler.setFormatter(GCPJsonFormatter())
    else:
        stream_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [rs-alerts] %(message)s",
            datefmt="%H:%M:%S",
        ))

    logging.basicConfig(level=log_level, handlers=[stream_handler], force=True)
