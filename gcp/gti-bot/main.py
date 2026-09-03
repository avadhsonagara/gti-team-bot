"""
FastAPI application & Google Cloud Run function (2nd Gen) entrypoint for GTI Teams Bot.

Handles:
  - Microsoft Teams Webhook endpoint: POST /api/messages
  - Root info & health check: GET /, GET /health
  - OPTIONS /api/messages for CORS/preflight
  - Cloud Run function HTTP entrypoint: gti_bot_http
"""
import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager

import functions_framework
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from werkzeug.wrappers import Response as WerkzeugResponse

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
#
# functions_framework serves this module through gunicorn (Flask/WSGI) —
# gti_bot_http below has a plain synchronous signature: one call in, one
# Response out. The Teams SDK app (`api`, `teams_app`) is async-only, so
# something still has to bridge into it; the bridge lives entirely in this
# section rather than depending on a third-party WSGI-to-ASGI package.

_loop = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """
    Lazily start one persistent background event loop, the first time this
    worker actually serves a request — not at module import time.

    gunicorn forks worker processes after importing this module; os.fork()
    only carries over the thread that calls it, so a loop/thread started at
    import time would silently stop existing in every forked worker, and
    every request would then wait forever for a thread that isn't actually
    running there. Starting it here, on first use inside the worker,
    guarantees the thread that answers requests is the one actually alive
    in this process.
    """
    global _loop
    if _loop is None:
        with _loop_lock:
            if _loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, daemon=True).start()
                # FastAPI's `lifespan` above never fires under this
                # Flask/WSGI entrypoint (no ASGI "lifespan" scope is ever
                # sent here), so run Teams App initialization here instead
                # — once, on this worker's now-live loop, before it serves
                # its first request.
                asyncio.run_coroutine_threadsafe(teams_app.initialize(), loop).result()
                logger.info("Teams App initialized (first request in this worker).")
                _loop = loop
    return _loop


def _build_scope(environ: dict) -> dict:
    """WSGI environ -> minimal ASGI HTTP scope (method, path, headers, ...)."""
    headers = [
        (
            (key[5:] if key.startswith("HTTP_") else key).lower().replace("_", "-").encode("latin-1"),
            value.encode("latin-1"),
        )
        for key, value in environ.items()
        if (key.startswith("HTTP_") and key not in ("HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"))
        or key in ("CONTENT_TYPE", "CONTENT_LENGTH")
    ]
    root_path = environ.get("SCRIPT_NAME", "")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").split("/")[-1],
        "method": environ["REQUEST_METHOD"],
        "scheme": environ.get("wsgi.url_scheme", "http"),
        "path": root_path + environ.get("PATH_INFO", ""),
        "root_path": root_path,
        "query_string": environ.get("QUERY_STRING", "").encode("ascii"),
        "server": (environ.get("SERVER_NAME", ""), int(environ.get("SERVER_PORT") or 0)),
        "headers": headers,
        "extensions": {},
    }
    if environ.get("REMOTE_ADDR") and environ.get("REMOTE_PORT"):
        scope["client"] = (environ["REMOTE_ADDR"], int(environ["REMOTE_PORT"]))
    return scope


async def _call_asgi_app(scope: dict, body: bytes):
    """Run exactly one ASGI request/response cycle against `api` to completion."""
    request_sent = False
    messages: list = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await api(scope, receive, send)

    status = 500
    response_headers: list = []
    response_body = b""
    for message in messages:
        if message["type"] == "http.response.start":
            status = message["status"]
            response_headers = message["headers"]
        elif message["type"] == "http.response.body":
            response_body += message.get("body", b"")
    return status, response_headers, response_body


@functions_framework.http
def gti_bot_http(request):
    """
    HTTP entrypoint for Google Cloud Run function (2nd Gen).

    Plain synchronous signature — one call in, one Response out — matching
    the Flask/WSGI contract functions_framework expects. Internally hands
    the request to the FastAPI/Teams SDK app via a single persistent event
    loop shared by every request this worker serves (not a fresh loop per
    call — the Teams SDK builds long-lived async HTTP clients that must
    stay bound to the same loop for their whole life).
    """
    loop = _get_loop()
    scope = _build_scope(request.environ)
    body = request.get_data()
    status, headers, body_out = asyncio.run_coroutine_threadsafe(
        _call_asgi_app(scope, body), loop
    ).result()
    return WerkzeugResponse(
        body_out,
        status=status,
        headers=[(name.decode("latin-1"), value.decode("latin-1")) for name, value in headers],
    )


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
