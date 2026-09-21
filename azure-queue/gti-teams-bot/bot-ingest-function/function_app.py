"""
Azure Functions HTTP entry point for Teams Bot message ingestion.

Handles inbound Bot Framework webhook activities, validates authorization tokens,
posts immediate acknowledgment placeholders, and enqueues query jobs to Azure Storage Queue.
"""
import json
import logging
import re
import threading
import time

import azure.functions as func
from azure.core.exceptions import ResourceExistsError
from azure.storage.queue import QueueClient

from app.config import settings
from app.constants import PLACEHOLDER_TEXT
from app.logging_config import setup_logging
from app.observability import bind_request, clear_request
from app.queue_job import build_job_payload
from app.teams.activity import parse_activity
from app.teams.auth import BotFrameworkAuthError, validate_bot_framework_token
from app.teams.context import Ctx

setup_logging()

logger = logging.getLogger("gti-teams-bot")

logger.info(
    "GTI Teams Bot Ingest Function starting | client_id=%s managed_identity=%s queue=%s",
    "set" if settings.client_id else "MISSING",
    "set" if settings.managed_identity_client_id else "MISSING",
    settings.job_queue_name,
)

app = func.FunctionApp()

_MENTION_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE)

_EMPTY_QUERY_NOTICE = (
    "👋 **How can I help you with threat intelligence?**\n\n"
    "Try asking something like:\n"
    "- _what do you know about 1.1.1.1?_\n"
    "- _analyze this domain: example.com_\n"
    "- _check hash: 275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0_\n"
    "- _give me threat intelligence on APT29_"
)
_JOB_TOO_LARGE_NOTICE = (
    "📁 **Message Too Large**\n\n"
    "This message exceeds the maximum allowed size. Please shorten your message or remove long quoted text and try again."
)

_QUEUE_FAILURE_NOTICE = (
    "⚠️ **Unable to Submit Request**\n\nWe couldn't submit your request right now. Please try again in a moment."
)

# Both the QueueClient instance and its create_queue() call only need to
# happen once per instance lifetime — without this cache, every single
# inbound message constructed a fresh client and re-issued create_queue() as
# an extra HTTP round-trip.
_queue_client_instance: QueueClient | None = None
_queue_client_lock = threading.Lock()


def _json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    """
    Construct an HTTP response containing a JSON payload.

    Args:
        payload: Dictionary to serialize as JSON.
        status_code: HTTP status code.

    Returns:
        func.HttpResponse with application/json MIME type.
    """
    return func.HttpResponse(json.dumps(payload), status_code=status_code, mimetype="application/json")


def _strip_mentions(text: str) -> str:
    """
    Remove mention tokens (<at>...</at>) from a Teams message string.

    Args:
        text: Raw message text containing mention markup.

    Returns:
        Cleaned text without mention tags.
    """
    return _MENTION_RE.sub("", text or "")


@app.route(route="/", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def root(req: func.HttpRequest) -> func.HttpResponse:
    """
    Return service identity, version, and messaging endpoint metadata.

    Args:
        req: Inbound HTTP request.

    Returns:
        JSON response with service information.
    """
    return _json_response({
        "status": "ok",
        "name": "Google Threat Intelligence Agentic Bot — Ingest (Azure)",
        "version": "1.0.0",
        "platform": "Azure Functions",
        "messaging_endpoint": "/api/messages",
    })


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    """
    Return service health status and configuration verification.

    Args:
        req: Inbound HTTP request.

    Returns:
        JSON response with health and queue configuration status.
    """
    return _json_response({
        "status": "ok",
        "app": "gti-teams-bot-ingest",
        "platform": "azure",
        "queue": settings.job_queue_name,
        "storage_configured": bool(settings.azure_web_jobs_storage),
    })


@app.route(route="api/messages", methods=["GET", "POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def messages(req: func.HttpRequest) -> func.HttpResponse:
    """
    Handle Microsoft Teams / Bot Framework activities.

    Supports OPTIONS preflight, GET status confirmation, and POST activity ingestion.

    Args:
        req: Inbound HTTP request containing Bot Framework payload.

    Returns:
        func.HttpResponse acknowledging receipt (200 OK) or error status.
    """
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200)

    if req.method == "GET":
        return _json_response({
            "message": "Teams Bot messaging endpoint is active and listening for POST requests.",
        })

    # POST — an inbound Bot Framework activity.

    # Early guard: this endpoint is publicly reachable (auth_level=ANONYMOUS
    # — the bot's own Bearer-token check below is what actually gates it),
    # so it will get arbitrary/abusive traffic. Reject an implausibly large
    # body outright, before spending any CPU parsing it as JSON.
    raw_body = req.get_body()
    if len(raw_body) > settings.max_request_body_bytes:
        logger.warning("[GUARD] Rejecting oversized request body (%d bytes)", len(raw_body))
        return _json_response({"error": "Request body too large"}, 413)

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (ValueError, UnicodeDecodeError):
        return _json_response({"error": "Invalid JSON body"}, 400)

    if not isinstance(body, dict):
        return _json_response({"error": "Invalid request body"}, 400)

    try:
        validate_bot_framework_token(
            req.headers.get("Authorization", ""),
            settings.client_id,
            body.get("serviceUrl"),
        )
    except BotFrameworkAuthError as exc:
        logger.warning("[AUTH] Rejected /api/messages request: %s", exc)
        return _json_response({"error": "Unauthorized"}, 401)

    if body.get("type") == "installationUpdate" and body.get("action") == "remove":
        # The bot was uninstalled from a team — enqueue a cleanup job so the
        # Worker Function App can delete that team's stored GTI sessions
        # (app/gti/session_store.py) instead of leaving them in Table
        # Storage forever. No placeholder, no user reply — nothing to say
        # for an uninstall event.
        try:
            _enqueue_installation_removed(body)
        except Exception:
            logger.exception("[ERROR] Failed to enqueue installationUpdate removal.")
        return func.HttpResponse(status_code=200)

    if body.get("type") != "message":
        # conversationUpdate, typing, etc. — nothing to do.
        logger.info("[EVENT] Ignoring non-message activity | type=%s", body.get("type"))
        return func.HttpResponse(status_code=200)

    t_start = time.perf_counter()
    try:
        _ingest_message(body, t_start)
    except Exception:
        # Anything unanticipated here must still ack with 200 — Bot Framework
        # retries a non-2xx response, which would re-run this whole ingest
        # path (and could double-post a placeholder or double-enqueue) for a
        # request that may have already been partially handled.
        logger.exception("[ERROR] Unhandled exception ingesting activity after %.0fms.", (time.perf_counter() - t_start) * 1000)
    finally:
        clear_request()

    return func.HttpResponse(status_code=200)


def _ingest_message(body: dict, t_start: float) -> None:
    """
    Process and validate an inbound user message activity.

    Binds request context, posts an immediate placeholder response, and enqueues
    the query job for background processing by the worker function.

    Args:
        body: Inbound activity JSON dictionary.
        t_start: Performance counter timestamp at request receipt.
    """
    activity = parse_activity(body)
    ctx = Ctx(activity)

    conversation_id = activity.conversation.id
    sender = getattr(activity, "from_", None)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"
    user_name = (getattr(sender, "name", None) or user_id) if sender else "unknown"
    scope = getattr(activity.conversation, "conversation_type", "") or ""
    user_text = _strip_mentions(activity.text or "").strip()

    bind_request(
        request_id=activity.id or "",
        user=user_id,
        user_name=user_name,
        query=user_text,
        scope=scope,
        conversation=conversation_id,
        activity_id=activity.id or "",
    )

    if not user_text or not re.search(r"\w", user_text, re.UNICODE):
        logger.info(
            "[INGEST] Empty query received | user='%s' (%s) scope=%s | sent usage hint in %.0fms",
            user_name, user_id, scope, (time.perf_counter() - t_start) * 1000,
        )
        try:
            ctx.send(_EMPTY_QUERY_NOTICE)
        except Exception:
            logger.warning("[INGEST] Failed to send empty-query usage hint to user='%s'", user_name)
        return

    raw_attachments = activity.attachments or []
    file_attachments = [
        a for a in raw_attachments
        if getattr(a, "content_type", "") and not getattr(a, "content_type", "").startswith(("text/html", "application/vnd.microsoft.card."))
    ]
    attachment_count = len(file_attachments)
    att_names = [getattr(a, "name", "") for a in file_attachments if getattr(a, "name", None)]
    att_info = f" | attachments={attachment_count} ({', '.join(att_names)})" if att_names else (f" | attachments={attachment_count}" if attachment_count else "")

    logger.info(
        "[INGEST 1/3] Inbound User Query | user='%s' (%s) scope=%s | query='%s'%s",
        user_name, user_id, scope, user_text, att_info,
    )

    # Channel messages already show the original post inline (and, for
    # thread replies, Teams renders the reply-to preview itself) — the
    # quoted-query blockquote is only useful in personal/group chats, which
    # have neither. Must match bot-worker-function's own quoting exactly:
    # the worker re-derives the same quoted_query independently when it
    # builds the final response, and the two need to visually match.
    if scope == "channel":
        quoted_query = ""
    else:
        quote_lines = [f"> {line}" for line in user_text.splitlines()] or ["> "]
        quoted_query = "\n".join(quote_lines)

    placeholder_text = f"{quoted_query}\n\n{PLACEHOLDER_TEXT}" if quoted_query else PLACEHOLDER_TEXT

    t_ph = time.perf_counter()
    loading_activity_id = None
    try:
        sent = ctx.send(placeholder_text)
        loading_activity_id = getattr(sent, "id", None)
        logger.info(
            "[INGEST 2/3] Placeholder posted in %.0fms | placeholder_id=%s conversation=%s",
            (time.perf_counter() - t_ph) * 1000, loading_activity_id, conversation_id,
        )
    except Exception as exc:
        logger.warning(
            "[INGEST 2/3] Placeholder post failed (%.0fms): %s | user='%s' query='%s'",
            (time.perf_counter() - t_ph) * 1000, exc, user_name, user_text,
        )

    _enqueue_job(
        body,
        loading_activity_id,
        ctx,
        scope,
        t_start,
        quoted_query,
        user_name=user_name,
        user_text=user_text,
    )


def _deliver_error_notice(ctx: Ctx, loading_activity_id, scope: str, text: str, quoted_query: str = "") -> None:
    """
    Deliver a minimal error notification to the user, replacing or deleting the placeholder.

    Args:
        ctx: Activity context instance.
        loading_activity_id: Activity ID of the placeholder message.
        scope: Conversation scope ('channel', 'personal', 'groupChat').
        text: Error message text.
        quoted_query: Optional quoted user query prefix.
    """
    if quoted_query:
        text = f"{quoted_query}\n\n{text}"
    try:
        if loading_activity_id and scope == "channel":
            ctx.api.conversations.activities(ctx.activity.conversation.id).update(loading_activity_id, text)
            return

        if loading_activity_id:
            try:
                ctx.api.conversations.activities(ctx.activity.conversation.id).delete(loading_activity_id)
            except Exception as exc:
                logger.warning("[ERROR] Could not delete placeholder before sending error notice (%s) — sending fresh message anyway.", exc)

        ctx.send(text)
    except Exception:
        logger.exception("[ERROR] Failed to deliver error notice to the user.")


def _get_queue_client() -> QueueClient:
    """
    Return a cached singleton QueueClient instance, creating the queue if needed.

    Returns:
        QueueClient instance for the job queue.
    """
    global _queue_client_instance
    if _queue_client_instance is None:
        with _queue_client_lock:
            if _queue_client_instance is None:
                client = QueueClient.from_connection_string(
                    settings.azure_web_jobs_storage, settings.job_queue_name,
                )
                try:
                    client.create_queue()
                except ResourceExistsError:
                    pass
                _queue_client_instance = client
    return _queue_client_instance


def _enqueue_job(
    activity_body: dict,
    loading_activity_id,
    ctx: Ctx,
    scope: str,
    t_start: float,
    quoted_query: str = "",
    user_name: str = "unknown",
    user_text: str = "",
) -> None:
    """
    Serialize and send a query job payload to Azure Storage Queue.

    Args:
        activity_body: Inbound activity JSON payload.
        loading_activity_id: Activity ID of the posted placeholder message.
        ctx: Context instance for delivering error notice if queueing fails.
        scope: Conversation scope.
        t_start: Performance counter timestamp when request was received.
        quoted_query: Quoted query string.
        user_name: Sender user name.
        user_text: Extracted user query string.
    """
    payload = build_job_payload(activity_body, loading_activity_id)
    encoded = json.dumps(payload)
    encoded_bytes = encoded.encode("utf-8")

    if len(encoded_bytes) > settings.max_job_payload_bytes:
        logger.error(
            "[INGEST] Job payload too large (%d bytes, limit %d) | user='%s' query='%s' — notifying user instead of enqueueing.",
            len(encoded_bytes), settings.max_job_payload_bytes, user_name, user_text,
        )
        _deliver_error_notice(ctx, loading_activity_id, scope, _JOB_TOO_LARGE_NOTICE, quoted_query)
        return

    try:
        t_q = time.perf_counter()
        queue_client = _get_queue_client()
        queue_client.send_message(encoded)
        logger.info(
            "[INGEST 3/3] Enqueued to %s in %.0fms | payload_size=%d bytes",
            settings.job_queue_name, (time.perf_counter() - t_q) * 1000, len(encoded_bytes),
        )
        logger.info(
            "[INGEST DONE] Handoff completed in %.0fms | user='%s' query='%s' ready for worker pickup",
            (time.perf_counter() - t_start) * 1000, user_name, user_text,
        )
    except Exception:
        logger.exception(
            "[INGEST] Failed to enqueue job after %.0fms | user='%s' query='%s' — notifying user.",
            (time.perf_counter() - t_start) * 1000, user_name, user_text,
        )
        _deliver_error_notice(ctx, loading_activity_id, scope, _QUEUE_FAILURE_NOTICE, quoted_query)


def _enqueue_installation_removed(activity_body: dict) -> None:
    """
    Enqueue a cleanup job to remove stored team sessions when the bot is uninstalled.

    Args:
        activity_body: Inbound installationUpdate activity JSON payload.
    """
    activity = parse_activity(activity_body)
    conversation_id = getattr(activity.conversation, "id", "")
    bind_request(request_id=activity.id or "", conversation=conversation_id, activity_id=activity.id or "")
    payload = build_job_payload(activity_body, None, kind="installationUpdateRemove")
    queue_client = _get_queue_client()
    queue_client.send_message(json.dumps(payload))
    logger.info("[INGEST EVENT] Enqueued installationUpdate removal for team session cleanup | conversation=%s", conversation_id)
