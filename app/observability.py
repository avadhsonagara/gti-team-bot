"""
Per-request observability context.

A single Teams query produces multiple log lines across handlers, GTI API,
and delivery. This module ties them together: a `request_id` (and the
GCP trace, when running on Cloud Run / Functions) is stored in a ContextVar at
the start of the handler and auto-injected into every log record by
`RequestContextFilter`.
"""
import contextvars
import logging
import os
import uuid

_request_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "request_ctx", default={}
)

_CONTEXT_FIELDS = ("activity_id", "user", "conversation", "tenant", "trace", "session_id")


def _gcp_project() -> str:
    """Resolve the GCP project id from the environment."""
    return (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    )


def _parse_trace_id(header: str) -> str:
    """
    Extract the 32-char hex trace id from an X-Cloud-Trace-Context header.
    Header format: "TRACE_ID/SPAN_ID;o=1".
    """
    if not header:
        return ""
    return header.split("/", 1)[0].strip()


def bind_request(*, trace_header: str = "", **fields) -> None:
    """
    Merge fields into the current request context (creating it if needed).
    """
    ctx = dict(_request_ctx.get())

    if "request_id" not in ctx and "request_id" not in fields:
        ctx["request_id"] = uuid.uuid4().hex

    if trace_header:
        trace_id = _parse_trace_id(trace_header)
        project = _gcp_project()
        if trace_id and project:
            ctx["trace"] = f"projects/{project}/traces/{trace_id}"

    ctx.update(fields)
    _request_ctx.set(ctx)


def clear_request() -> None:
    """Reset the context. Call in a finally block."""
    _request_ctx.set({})


class RequestContextFilter(logging.Filter):
    """Copies the current request context onto each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _request_ctx.get()
        record.request_id = ctx.get("request_id", "-")
        for field in _CONTEXT_FIELDS:
            if field in ctx:
                setattr(record, field, ctx[field])
        return True
