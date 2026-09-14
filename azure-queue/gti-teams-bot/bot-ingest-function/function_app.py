"""
Azure Functions (Python v2 programming model) entry point — Ingest Function.

Responsibilities, and nothing else:
  1. Verify the inbound Bot Framework JWT (app/teams/auth.py).
  2. Parse the raw Activity JSON (app/teams/activity.py).
  3. Post the "looking into that…" placeholder immediately
     (app/teams/context.py) so the user sees a response right away.
  4. Hand the activity off to the Worker Function App via a Storage Queue
     job (app/queue_job.py, bot-worker-function/) and acknowledge with 200.

Deliberately does NOT call the GTI Agentic API, fetch channel thread
context, or do anything else that can take more than a second or two —
that's bot-worker-function/'s job, which runs on its own timeout budget
completely decoupled from this HTTP request/response cycle. This is what
lets the bot answer a query that takes 5-10 minutes without needing a
Premium/long-idle-timeout ingress anywhere: this endpoint always responds in
well under a second.

`host.json` sets `extensions.http.routePrefix` to "" so routes are exposed
exactly as below (no extra "/api" prefix Azure adds by default) — this keeps
the Bot messaging endpoint at "/api/messages" to match the Azure Bot
resource configuration and the Teams app manifest.

`auth_level=ANONYMOUS` is intentional: Azure Bot Service calls the messaging
endpoint without an Azure Functions key. Authenticity of inbound activities
is verified inside app/teams/auth.py instead, using CLIENT_ID (the bot's App
ID) — the same security model as bot-worker-function/ and azure/azure-bot-function.
"""
import json
import logging
import re
import threading

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
    "📁 **Request Too Large**\n\n"
    "This message is too large for the bot to queue for processing (likely a very long "
    "quoted thread or attachment list). Please shorten your message and try again."
)
_QUEUE_FAILURE_NOTICE = (
    "⚠️ **Something went wrong while queuing your request.** Please try again in a moment."
)

# Both the QueueClient instance and its create_queue() call only need to
# happen once per instance lifetime — without this cache, every single
# inbound message constructed a fresh client and re-issued create_queue() as
# an extra HTTP round-trip.
_queue_client_instance: QueueClient | None = None
_queue_client_lock = threading.Lock()


def _json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload), status_code=status_code, mimetype="application/json")


def _strip_mentions(text: str) -> str:
    """Remove all <at>...</at> mention tokens from a Teams message."""
    return _MENTION_RE.sub("", text or "")


@app.route(route="/", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def root(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({
        "status": "ok",
        "name": "Google Threat Intelligence Agentic Bot — Ingest (Azure)",
        "version": "1.0.0",
        "platform": "Azure Functions",
        "messaging_endpoint": "/api/messages",
    })


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({
        "status": "ok",
        "app": "gti-teams-bot-ingest",
        "platform": "azure",
        "queue": settings.job_queue_name,
        "storage_configured": bool(settings.azure_web_jobs_storage),
    })


@app.route(route="api/messages", methods=["GET", "POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def messages(req: func.HttpRequest) -> func.HttpResponse:
    """Microsoft Teams / Bot Framework webhook endpoint."""
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

    if body.get("type") != "message":
        # conversationUpdate, typing, installationUpdate, etc. — nothing to do.
        return func.HttpResponse(status_code=200)

    try:
        _ingest_message(body)
    except Exception:
        # Anything unanticipated here must still ack with 200 — Bot Framework
        # retries a non-2xx response, which would re-run this whole ingest
        # path (and could double-post a placeholder or double-enqueue) for a
        # request that may have already been partially handled.
        logger.exception("[ERROR] Unhandled exception ingesting activity.")
    finally:
        clear_request()

    return func.HttpResponse(status_code=200)


def _ingest_message(body: dict) -> None:
    activity = parse_activity(body)
    ctx = Ctx(activity)

    conversation_id = activity.conversation.id
    sender = getattr(activity, "from_", None)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"
    bind_request(user=user_id, conversation=conversation_id, activity_id=activity.id or "")

    scope = getattr(activity.conversation, "conversation_type", "") or ""
    user_text = _strip_mentions(activity.text or "").strip()

    if not user_text or not re.search(r"\w", user_text, re.UNICODE):
        logger.info("[EVENT] Message with no meaningful query — replying directly, no queue needed.")
        try:
            ctx.send(_EMPTY_QUERY_NOTICE)
        except Exception:
            logger.warning("[EVENT] Failed to send empty-query usage hint.")
        return

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

    loading_activity_id = None
    try:
        sent = ctx.send(placeholder_text)
        loading_activity_id = getattr(sent, "id", None)
        logger.info("[PLACEHOLDER] Posted | id=%s conversation=%s", loading_activity_id, conversation_id)
    except Exception as exc:
        logger.warning("[PLACEHOLDER] Failed (%s) — worker will fall back to a fresh send.", exc)

    _enqueue_job(body, loading_activity_id, ctx, scope)


def _deliver_error_notice(ctx: Ctx, loading_activity_id, scope: str, text: str) -> None:
    """Minimal, card-free error delivery — this function never needs the rich Adaptive Card path."""
    try:
        if loading_activity_id and scope == "channel":
            ctx.api.conversations.activities(ctx.activity.conversation.id).update(loading_activity_id, text)
            return

        if loading_activity_id:
            # Isolated from the ctx.send() below: a delete failure here (the
            # placeholder was already gone, expired, or a transient error)
            # must not skip sending the notice — the user still needs to
            # hear that something went wrong either way.
            try:
                ctx.api.conversations.activities(ctx.activity.conversation.id).delete(loading_activity_id)
            except Exception as exc:
                logger.warning("[ERROR] Could not delete placeholder before sending error notice (%s) — sending fresh message anyway.", exc)

        ctx.send(text)
    except Exception:
        logger.exception("[ERROR] Failed to deliver error notice to the user.")


def _enqueue_job(activity_body: dict, loading_activity_id, ctx: Ctx, scope: str) -> None:
    payload = build_job_payload(activity_body, loading_activity_id)
    encoded = json.dumps(payload)
    encoded_bytes = encoded.encode("utf-8")

    if len(encoded_bytes) > settings.max_job_payload_bytes:
        logger.error(
            "[QUEUE] Job payload too large (%d bytes, limit %d) — notifying user instead of enqueueing.",
            len(encoded_bytes), settings.max_job_payload_bytes,
        )
        _deliver_error_notice(ctx, loading_activity_id, scope, _JOB_TOO_LARGE_NOTICE)
        return

    global _queue_client_instance
    try:
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
        queue_client = _queue_client_instance
        # Sent as plain UTF-8 text (no base64/encoding policy) — the worker's
        # native queue_trigger binding (bot-worker-function/function_app.py)
        # reads it back the same way via msg.get_body().decode("utf-8"), so
        # both sides agree on the wire format without any extra framing.
        queue_client.send_message(encoded)
        logger.info("[QUEUE] Enqueued job (%d bytes) for conversation=%s", len(encoded_bytes), ctx.activity.conversation.id)
    except Exception:
        logger.exception("[QUEUE] Failed to enqueue job — notifying user.")
        _deliver_error_notice(ctx, loading_activity_id, scope, _QUEUE_FAILURE_NOTICE)
