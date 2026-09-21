"""
Per-request observability context management.

Stores request-scoped context variables (e.g. request ID, user, query, conversation)
and injects them into standard logging records via RequestContextFilter.
"""
import contextvars
import logging
import uuid

_request_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "request_ctx", default={}
)

_CONTEXT_FIELDS = (
    "activity_id",
    "user",
    "user_name",
    "query",
    "scope",
    "conversation",
    "tenant",
    "session_id",
)


def bind_request(**fields) -> None:
    """
    Bind context key-value pairs to the current asynchronous request context.

    Args:
        **fields: Keyword arguments representing context properties to associate with the request.
    """
    ctx = dict(_request_ctx.get())

    req_id = fields.pop("request_id", None) or ctx.get("request_id") or uuid.uuid4().hex
    ctx["request_id"] = req_id

    ctx.update({k: v for k, v in fields.items() if v is not None and v != ""})
    _request_ctx.set(ctx)


def clear_request() -> None:
    """Clear the current request context variables."""
    _request_ctx.set({})


class RequestContextFilter(logging.Filter):
    """Logging filter that copies current request context attributes onto each LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach context fields to the log record for formatting and telemetry export."""
        ctx = _request_ctx.get()
        record.request_id = ctx.get("request_id") or "-"
        for field in _CONTEXT_FIELDS:
            if field in ctx:
                setattr(record, field, ctx[field])
        return True
