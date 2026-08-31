"""
FastAPI application & Google Cloud Run function (2nd Gen) entrypoint for GTI Teams Bot.

Handles:
  - Microsoft Teams Webhook endpoint: POST /api/messages
  - Root info & health check: GET /, GET /health
  - OPTIONS /api/messages for CORS/preflight
  - Cloud Run function HTTP entrypoint: gti_bot_http
"""
import logging
import os
from contextlib import asynccontextmanager

from a2wsgi import ASGIMiddleware
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import functions_framework
from werkzeug.wrappers import Response

from app.config import settings
from app.gti.client import gti_client
from app.logging_config import setup_logging
from app.teams.bot import create_teams_app

# Initialize logging before other imports
setup_logging()

logger = logging.getLogger("gti-teams-bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log startup/shutdown state and initialize Teams App."""
    logger.info(
        "GTI Teams Bot (Agentic - GCP) starting | project=%s gti_url=%s gti_key=%s client_id=%s tenant_id=%s",
        settings.gcp_project_id,
        settings.gti_api_base_url,
        "set" if settings.gti_api_key else "MISSING",
        "set" if settings.client_id else "MISSING",
        "set" if settings.tenant_id else "MISSING",
    )
    await teams_app.initialize()
    yield
    await gti_client.close()
    logger.info("GTI Teams Bot (Agentic - GCP) shut down cleanly.")


api = FastAPI(title="GTI Teams Bot (Agentic - GCP)", lifespan=lifespan)

# FastAPIAdapter mounts POST /api/messages onto `api`
teams_app = create_teams_app(api)


@api.get("/")
async def root():
    return JSONResponse({
        "status": "ok",
        "name": "Google Threat Intelligence Agentic Bot (GCP)",
        "version": "1.0.0",
        "platform": "Google Cloud Run / Cloud Functions Gen 2",
        "messaging_endpoint": "/api/messages",
    })


@api.get("/health")
async def health_check():
    """Liveness probe for Cloud Run, Cloud Functions, or uptime monitors."""
    return JSONResponse({
        "status": "ok",
        "app": "gti-teams-bot-agentic",
        "platform": "gcp",
        "gti_api_configured": bool(settings.gti_api_key),
    })


@api.options("/api/messages")
async def options_messages():
    """Handle CORS/OPTIONS preflight requests from Bot Framework or proxies."""
    return JSONResponse(content={}, status_code=200)


@api.get("/api/messages")
async def get_messages():
    """Information endpoint for GET requests to /api/messages."""
    return JSONResponse({
        "message": "Teams Bot messaging endpoint is active and listening for POST requests.",
    })


# ── Cloud Run function (2nd Gen) Entry Point ─────────────────────────────────

wsgi_app = ASGIMiddleware(api)


@functions_framework.http
def gti_bot_http(request):
    """
    HTTP entrypoint for Google Cloud Run function (2nd Gen).

    Bridges incoming Werkzeug/Flask requests to the ASGI FastAPI application
    via ASGIMiddleware.
    """
    return Response.from_app(wsgi_app, request.environ)


if __name__ == "__main__":
    import uvicorn

    ssl_kwargs = {}
    keyfile = settings.ssl_keyfile or ("cert.key" if os.path.exists("cert.key") else None)
    certfile = settings.ssl_certfile or (
        "fullchain.cer" if os.path.exists("fullchain.cer")
        else ("cert.cer" if os.path.exists("cert.cer") else None)
    )

    if keyfile and certfile and os.path.exists(keyfile) and os.path.exists(certfile):
        ssl_kwargs["ssl_keyfile"] = keyfile
        ssl_kwargs["ssl_certfile"] = certfile
        logger.info("SSL enabled using certfile=%s and keyfile=%s", certfile, keyfile)

    uvicorn.run("main:api", host="0.0.0.0", port=settings.port, log_config=None, **ssl_kwargs)
