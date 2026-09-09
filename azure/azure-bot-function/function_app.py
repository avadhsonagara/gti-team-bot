"""
Azure Functions (Python v2 programming model) entry point.

Fully synchronous, with no dependency on the microsoft-teams-apps SDK: this
hand-rolls the pieces the SDK previously provided —
  - app/teams/auth.py       — verifying the inbound Bot Framework JWT
  - app/teams/activity.py   — parsing the raw Activity JSON
  - app/teams/context.py    — sending/editing/deleting Teams messages
  - app/teams/bot_client.py — the bot's own outbound Connector API token
    (Managed Identity in Azure, client-secret fallback for local dev)
— using plain `requests`, the same style as azure/rs-alerts.

`host.json` sets `extensions.http.routePrefix` to "" so routes are exposed
exactly as below (no extra "/api" prefix Azure adds by default) — this keeps
the Bot messaging endpoint at "/api/messages" to match the Azure Bot
resource configuration and the Teams app manifest.

`auth_level=ANONYMOUS` is intentional: Azure Bot Service calls the messaging
endpoint without an Azure Functions key. Authenticity of inbound activities
is verified inside app/teams/auth.py instead, using CLIENT_ID (the bot's App
ID) — the same security model as the FastAPI/SDK-hosted version, minus the
SDK.
"""
import json
import logging

import azure.functions as func

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
    "GTI Teams Bot (Agentic - Azure Functions) starting | gti_url=%s gti_key=%s client_id=%s tenant_id=%s managed_identity=%s",
    settings.gti_api_base_url,
    "set" if settings.gti_api_key else "MISSING",
    "set" if settings.client_id else "MISSING",
    "set" if settings.tenant_id else "MISSING",
    "set" if settings.managed_identity_client_id else "not set (local-dev client-secret mode)",
)

app = func.FunctionApp()


def _json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload), status_code=status_code, mimetype="application/json")


@app.route(route="/", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def root(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({
        "status": "ok",
        "name": "Google Threat Intelligence Agentic Bot (Azure)",
        "version": "1.0.0",
        "platform": "Azure Functions",
        "messaging_endpoint": "/api/messages",
    })


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({
        "status": "ok",
        "app": "gti-teams-bot-agentic",
        "platform": "azure",
        "gti_api_configured": bool(settings.gti_api_key),
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
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Invalid JSON body"}, 400)

    if not isinstance(body, dict):
        # This endpoint is publicly reachable (auth_level=ANONYMOUS — the
        # bot's own Bearer-token check is what actually gates it), so it
        # will get arbitrary/malformed traffic. Anything that isn't a JSON
        # object can't be a real Activity, and body.get(...) below would
        # otherwise raise AttributeError on a str/int/bool/list body.
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

    return func.HttpResponse(status_code=200)
