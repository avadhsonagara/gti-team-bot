"""
Per-request observability context.

A single Teams query produces multiple log lines across handlers, GTI API,
and delivery. This module ties them together: a `request_id` is stored in a
ContextVar at the start of the handler and auto-injected into every log
record by `RequestContextFilter`.
"""
import contextvars
import logging
import uuid

_request_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "request_ctx", default={}
)

_CONTEXT_FIELDS = ("activity_id", "user", "conversation", "tenant", "session_id")


def bind_request(**fields) -> None:
    """
    Merge fields into the current request context (creating it if needed).
    """
    ctx = dict(_request_ctx.get())

    if "request_id" not in ctx and "request_id" not in fields:
        ctx["request_id"] = uuid.uuid4().hex

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
