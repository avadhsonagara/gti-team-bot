"""
Google Cloud Run function (2nd Gen) entrypoint for the GTI Teams Bot.

Fully synchronous — verifies the inbound Bot Framework JWT
(app/teams/auth.py), parses the Activity JSON (app/teams/activity.py), and
sends/edits/deletes Teams messages (app/teams/context.py, bot_client.py).

Handles:
  - Microsoft Teams Webhook endpoint: POST /api/messages
  - Root info & health check: GET /, GET /health
  - OPTIONS /api/messages for CORS/preflight
"""
import logging

import functions_framework
from flask import Request, jsonify

from app.config import settings
from app.logging_config import setup_logging
from app.teams.activity import parse_activity
from app.teams.auth import BotFrameworkAuthError, validate_bot_framework_token
from app.teams.context import Ctx
from app.teams.handlers import handle_installation_removed, handle_message

# Initialize logging before other imports
setup_logging()

logger = logging.getLogger("gti-teams-bot")

logger.info(
    "GTI Teams Bot (Agentic - GCP Cloud Run function) starting | project=%s gti_url=%s gti_key=%s client_id=%s tenant_id=%s",
    settings.gcp_project_id,
    settings.gti_api_base_url,
    "set" if settings.gti_api_key else "MISSING",
    "set" if settings.client_id else "MISSING",
    "set" if settings.tenant_id else "MISSING",
)


def _handle_messaging_endpoint(request: Request):
    """Validate, parse, and route one inbound Bot Framework activity."""
    # Early guard: this endpoint is publicly reachable (--allow-unauthenticated
    # — the bot's own Bearer-token check below is what actually gates it), so
    # it will get arbitrary/abusive traffic. Reject an implausibly large body
    # outright, before spending any CPU parsing it as JSON.
    raw_body = request.get_data()
    if len(raw_body) > settings.max_request_body_bytes:
        logger.warning("[GUARD] Rejecting oversized request body (%d bytes)", len(raw_body))
        return jsonify({"error": "Request body too large"}), 413

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        # This endpoint is publicly reachable (--allow-unauthenticated — the
        # bot's own Bearer-token check below is what actually gates it), so
        # it will get arbitrary/malformed traffic. Anything that isn't a
        # JSON object can't be a real Activity, and body.get(...) below
        # would otherwise raise AttributeError on a str/int/bool/list body.
        return jsonify({"error": "Invalid request body"}), 400

    try:
        validate_bot_framework_token(
            request.headers.get("Authorization", ""),
            settings.client_id,
            body.get("serviceUrl"),
        )
    except BotFrameworkAuthError as exc:
        logger.warning("[AUTH] Rejected /api/messages request: %s", exc)
        return jsonify({"error": "Unauthorized"}), 401

    if body.get("type") == "installationUpdate" and body.get("action") == "remove":
        try:
            handle_installation_removed(parse_activity(body))
        except Exception:
            logger.exception("[ERROR] Failed to process installationUpdate removal.")
        return "", 200

    if body.get("type") != "message":
        # conversationUpdate, typing, etc. — nothing to do.
        return "", 200

    try:
        ctx = Ctx(parse_activity(body))
        handle_message(ctx)
    except Exception:
        # handle_message already catches every GTI/delivery error it knows
        # about and shows the user a friendly card — this is a last resort
        # for anything unanticipated. Bot Framework retries a non-2xx
        # response, which for this bot means re-running an expensive GTI
        # query and likely double-posting a reply, so always ack with 200
        # rather than let an unknown exception surface as a 500.
        logger.exception("[ERROR] Unhandled exception processing inbound activity.")

    return "", 200


@functions_framework.http
def gti_bot_http(request: Request):
    """HTTP entrypoint for Google Cloud Run function (2nd Gen)."""
    path, method = request.path, request.method

    if path == "/" and method == "GET":
        return jsonify({
            "status": "ok",
            "name": "Google Threat Intelligence Agentic Bot (GCP)",
            "version": "1.0.0",
            "platform": "Google Cloud Run function",
            "messaging_endpoint": "/api/messages",
        })

    if path == "/health" and method == "GET":
        return jsonify({
            "status": "ok",
            "app": "gti-teams-bot-agentic",
            "platform": "gcp",
            "gti_api_configured": bool(settings.gti_api_key),
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
