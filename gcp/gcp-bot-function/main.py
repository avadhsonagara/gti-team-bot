"""
Google Cloud Run function (2nd Gen) entrypoint for the GTI Teams Bot.

Fully synchronous, with no dependency on the microsoft-teams-apps SDK: this
hand-rolls the pieces the SDK previously provided —
  - app/teams/auth.py     — verifying the inbound Bot Framework JWT
  - app/teams/activity.py — parsing the raw Activity JSON
  - app/teams/context.py  — sending/editing/deleting Teams messages
  - app/teams/bot_client.py — the bot's own outbound Connector API token
— using plain `requests`, the same style as gcp/rs-alerts. See those modules
for why (a prior version that kept the SDK hit "Event loop is closed" bugs
from its internals being reused across separate asyncio.run() calls).

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
from app.teams.handlers import handle_message

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
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        # This endpoint is publicly reachable (--allow-unauthenticated — the
        # SDK's own Bearer-token check is what actually gates it), so it
        # will get arbitrary/malformed traffic. Anything that isn't a JSON
        # object can't be a real Activity, and body.get(...) below would
        # otherwise raise AttributeError on a str/int/bool/list body.
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

    if body.get("type") != "message":
        # conversationUpdate, typing, installationUpdate, etc. — nothing to do.
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
