"""
FastAPI entry point — Azure Container Apps version of the GTI Teams Bot (Agentic).

This is the container equivalent of azure/azure-bot-function/function_app.py:
same app/ business logic package, served by uvicorn instead of the Azure
Functions host. See that file's docstring for the auth/routing model this
mirrors (no platform-level auth gate — the bot's own Bearer-token check in
app/teams/auth.py is what actually gates /api/messages).

Why a container instead of a Function: Azure Functions enforces a 230-second
HTTP response ceiling on every hosting plan (Consumption, Flex Consumption,
Premium, Dedicated) that no host.json setting can lift, because it's imposed
by the platform's load balancer, not the Functions runtime. A Container App
has no equivalent platform-imposed ceiling — the real limit becomes whatever
the Container Apps environment's ingress is configured for (see README.md —
Premium ingress mode's "Idle request timeout" must be raised past your
slowest real GTI call).

Fully async: every I/O call in app/ (GTI, Graph, Bot Framework Connector,
Blob/Table storage) is a native `await`, all the way down to httpx.AsyncClient
and the azure-sdk `.aio` clients — nothing here runs in a worker thread. A
single process can therefore hold open far more concurrent multi-minute GTI
calls than a thread-per-request model would allow.

Concurrency is deliberately not capped in-process (no semaphore, no thread
pool) — that's left to the Container Apps environment's HTTP scale rule
(`concurrentRequests` / `--scale-rule-http-concurrency`), the platform-level
equivalent of Cloud Run's `--concurrency`. See README.md for a note on how
that setting's semantics differ from Cloud Run's.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app import output_format_store
from app.config import settings
from app.gti import session_store
from app.gti.client import gti_client
from app.graph.client import graph_client
from app.logging_config import setup_logging
from app.teams import bot_client
from app.teams.activity import parse_activity
from app.teams.auth import BotFrameworkAuthError, validate_bot_framework_token
from app.teams.context import Ctx
from app.teams.handlers import handle_message

setup_logging()

logger = logging.getLogger("gti-teams-bot")

logger.info(
    "GTI Teams Bot (Agentic - Container App) starting | gti_url=%s gti_key=%s "
    "client_id=%s managed_identity=%s storage=%s gti_timeout=%.0fs",
    settings.gti_api_base_url,
    "set" if settings.gti_api_key else "MISSING",
    "set" if settings.client_id else "MISSING",
    "set" if settings.managed_identity_client_id else "MISSING",
    "set" if settings.storage_connection_string else "MISSING",
    settings.gti_timeout_seconds,
)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    yield
    # Close every shared async client cleanly on shutdown.
    await gti_client.close()
    await graph_client.close()
    await bot_client.close()
    await session_store.close()
    await output_format_store.close()


app = FastAPI(title="GTI Teams Bot (Agentic)", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


@app.get("/")
async def root() -> JSONResponse:
    return _json({
        "status": "ok",
        "name": "Google Threat Intelligence Agentic Bot (Azure)",
        "version": "1.0.0",
        "platform": "Azure Container Apps",
        "messaging_endpoint": "/api/messages",
    })


@app.get("/health")
async def health() -> JSONResponse:
    return _json({
        "status": "ok",
        "app": "gti-teams-bot-agentic",
        "platform": "azure-container-apps",
    })


@app.api_route("/api/messages", methods=["GET", "POST", "OPTIONS"])
async def messages(request: Request) -> Response:
    """Microsoft Teams / Bot Framework webhook endpoint."""
    if request.method == "OPTIONS":
        return Response(status_code=200)

    if request.method == "GET":
        return _json({
            "message": "Teams Bot messaging endpoint is active and listening for POST requests.",
        })

    # POST — an inbound Bot Framework activity.
    try:
        body = await request.json()
    except ValueError:
        return _json({"error": "Invalid JSON body"}, 400)

    if not isinstance(body, dict):
        # This endpoint is publicly reachable (no platform-level auth gate —
        # the bot's own Bearer-token check is what actually gates it), so it
        # will get arbitrary/malformed traffic. Anything that isn't a JSON
        # object can't be a real Activity, and body.get(...) below would
        # otherwise raise AttributeError on a str/int/bool/list body.
        return _json({"error": "Invalid request body"}, 400)

    try:
        validate_bot_framework_token(
            request.headers.get("authorization", ""),
            settings.client_id,
            body.get("serviceUrl"),
        )
    except BotFrameworkAuthError as exc:
        logger.warning("[AUTH] Rejected /api/messages request: %s", exc)
        return _json({"error": "Unauthorized"}, 401)

    if body.get("type") != "message":
        # conversationUpdate, typing, installationUpdate, etc. — nothing to do.
        return Response(status_code=200)

    try:
        ctx = Ctx(parse_activity(body))
        await handle_message(ctx)
    except Exception:
        # handle_message already catches every GTI/delivery error it knows
        # about and shows the user a friendly card — this is a last resort
        # for anything unanticipated. Bot Framework retries a non-2xx
        # response, which for this bot means re-running an expensive GTI
        # query and likely double-posting a reply, so always ack with 200
        # rather than let an unknown exception surface as a 500.
        logger.exception("[ERROR] Unhandled exception processing inbound activity.")

    return Response(status_code=200)
