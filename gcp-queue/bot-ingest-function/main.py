"""
Google Cloud Run function (2nd Gen) entrypoint for the GTI Teams Bot Ingest
function.

Verifies the inbound Bot Framework JWT (app/teams/auth.py), posts an
immediate "⏳ Looking into that…" placeholder, and publishes a job to Pub/Sub
for bot-worker-function to process — this function never calls GTI itself,
so it always answers Bot Framework well under its 15-second response
deadline regardless of how long the underlying GTI query would take.

  GET  /            — status
  GET  /health       — health check
  POST /api/messages — Bot Framework webhook
"""
import json
import logging
import re
import time

import functions_framework
from flask import Request, jsonify

from app.config import settings
from app.constants import PLACEHOLDER_TEXT
from app.logging_config import setup_logging
from app.observability import bind_request, clear_request
from app.queue_job import build_job_payload, publish_job
from app.teams.activity import parse_activity
from app.teams.auth import BotFrameworkAuthError, validate_bot_framework_token
from app.teams.context import Ctx

setup_logging()

logger = logging.getLogger("gti-teams-bot")

logger.info(
    "GTI Teams Bot Ingest Function (GCP) starting | client_id=%s topic=%s",
    "set" if settings.client_id else "MISSING",
    settings.pubsub_topic,
)

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
_PUBLISH_FAILURE_NOTICE = (
    "⚠️ **Unable to Submit Request**\n\nWe couldn't submit your request right now. Please try again in a moment."
)


def _strip_mentions(text: str) -> str:
    """Remove mention tokens (<at>...</at>) from a Teams message string."""
    return _MENTION_RE.sub("", text or "")


def _deliver_error_notice(ctx: Ctx, loading_activity_id, scope: str, text: str, quoted_query: str = "") -> None:
    """Deliver a minimal error notification, replacing or deleting the placeholder."""
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


def _publish_job(payload: dict, ctx: Ctx, loading_activity_id, scope: str, quoted_query: str, user_name: str, user_text: str, t_start: float) -> None:
    """Serialize and publish a job payload to Pub/Sub, falling back to a user-facing notice on failure."""
    encoded_bytes = json.dumps(payload).encode("utf-8")
    if len(encoded_bytes) > settings.max_job_payload_bytes:
        logger.error(
            "[INGEST] Job payload too large (%d bytes, limit %d) | user='%s' query='%s' — notifying user instead of publishing.",
            len(encoded_bytes), settings.max_job_payload_bytes, user_name, user_text,
        )
        _deliver_error_notice(ctx, loading_activity_id, scope, _JOB_TOO_LARGE_NOTICE, quoted_query)
        return

    try:
        t_pub = time.perf_counter()
        message_id = publish_job(payload)
        logger.info(
            "[INGEST 3/3] Published to %s in %.0fms | messageId=%s payload_size=%d bytes",
            settings.pubsub_topic, (time.perf_counter() - t_pub) * 1000, message_id, len(encoded_bytes),
        )
        logger.info(
            "[INGEST DONE] Handoff completed in %.0fms | user='%s' query='%s' ready for worker pickup",
            (time.perf_counter() - t_start) * 1000, user_name, user_text,
        )
    except Exception:
        logger.exception(
            "[INGEST] Failed to publish job after %.0fms | user='%s' query='%s' — notifying user.",
            (time.perf_counter() - t_start) * 1000, user_name, user_text,
        )
        _deliver_error_notice(ctx, loading_activity_id, scope, _PUBLISH_FAILURE_NOTICE, quoted_query)


def _ingest_message(body: dict, t_start: float) -> None:
    """Process and validate an inbound user message activity, then hand it off to the worker."""
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

    payload = build_job_payload(body, loading_activity_id)
    _publish_job(payload, ctx, loading_activity_id, scope, quoted_query, user_name, user_text, t_start)


def _handle_messaging_endpoint(request: Request):
    """Validate, parse, and route one inbound Bot Framework activity."""
    # Early guard: this endpoint is publicly reachable (Cloud Run ingress
    # ALLOW_ALL — the bot's own Bearer-token check below is what actually
    # gates it), so it will get arbitrary/abusive traffic. Reject an
    # implausibly large body outright, before spending any CPU parsing it
    # as JSON.
    raw_body = request.get_data()
    if len(raw_body) > settings.max_request_body_bytes:
        logger.warning("[GUARD] Rejecting oversized request body (%d bytes)", len(raw_body))
        return jsonify({"error": "Request body too large"}), 413

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid request body"}), 400

    try:
        validate_bot_framework_token(
            request.headers.get("Authorization", ""),
            settings.client_id,
            body.get("serviceUrl"),
            settings.tenant_id,
        )
    except BotFrameworkAuthError as exc:
        logger.warning("[AUTH] Rejected /api/messages request: %s", exc)
        return jsonify({"error": "Unauthorized"}), 401

    if body.get("type") == "installationUpdate" and body.get("action") == "remove":
        # The bot was uninstalled from a team — publish a cleanup job so the
        # worker can delete that team's stored GTI sessions
        # (app/gti/session_store.py) instead of leaving them in Firestore
        # forever. No placeholder, no user reply — nothing to say for an
        # uninstall event.
        try:
            activity = parse_activity(body)
            conversation_id = getattr(activity.conversation, "id", "")
            bind_request(request_id=activity.id or "", conversation=conversation_id, activity_id=activity.id or "")
            payload = build_job_payload(body, None, kind="installationUpdateRemove")
            publish_job(payload)
            logger.info("[INGEST EVENT] Published installationUpdate removal for team session cleanup | conversation=%s", conversation_id)
        except Exception:
            logger.exception("[ERROR] Failed to publish installationUpdate removal.")
        return "", 200

    if body.get("type") != "message":
        logger.info("[EVENT] Ignoring non-message activity | type=%s", body.get("type"))
        return "", 200

    t_start = time.perf_counter()
    try:
        _ingest_message(body, t_start)
    except Exception:
        # Anything unanticipated here must still ack with 200 — Bot
        # Framework retries a non-2xx response, which would re-run this
        # whole ingest path (and could double-post a placeholder or
        # double-publish) for a request that may have already been
        # partially handled.
        logger.exception("[ERROR] Unhandled exception ingesting activity after %.0fms.", (time.perf_counter() - t_start) * 1000)
    finally:
        clear_request()

    return "", 200


@functions_framework.http
def gti_bot_ingest_http(request: Request):
    """HTTP entrypoint for the Ingest Cloud Run function (2nd Gen)."""
    path, method = request.path, request.method

    if path == "/" and method == "GET":
        return jsonify({
            "status": "ok",
            "name": "Google Threat Intelligence Agentic Bot — Ingest (GCP)",
            "version": "1.0.0",
            "platform": "Google Cloud Run function",
            "messaging_endpoint": "/api/messages",
        })

    if path == "/health" and method == "GET":
        return jsonify({
            "status": "ok",
            "app": "gti-teams-bot-ingest",
            "platform": "gcp",
            "pubsub_topic": settings.pubsub_topic,
        })

    if path == "/api/messages" and method == "OPTIONS":
        return "", 200

    if path == "/api/messages" and method == "GET":
        return jsonify({
            "message": "Teams Bot messaging endpoint is active and listening for POST requests.",
        })

    if path == "/api/messages" and method == "POST":
        return _handle_messaging_endpoint(request)

    return jsonify({"error": "Not found"}), 404
