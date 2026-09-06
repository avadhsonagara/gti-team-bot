"""
Azure Functions (Python v2 programming model) entry point.

Wraps the existing FastAPI app defined in main.py as a single ASGI-backed
Azure Function using `azure.functions.AsgiFunctionApp`. That class:

  - Registers one HTTP-triggered function bound to route "{*route}" with
    every HTTP method, so all of main.py's routes (/, /health,
    /api/messages, OPTIONS /api/messages) keep working unchanged.
  - Drives the ASGI lifespan protocol on the first invocation
    (AsgiMiddleware.notify_startup()), which runs main.py's `lifespan()`
    context manager — the same `await teams_app.initialize()` /
    `await gti_client.close()` path used when running under uvicorn.

host.json sets `extensions.http.routePrefix` to "" so routes are exposed
exactly as in main.py (no extra "/api" prefix Azure adds by default) —
this keeps the Bot messaging endpoint at "/api/messages" to match the
Azure Bot resource configuration and the Teams app manifest.

`http_auth_level=ANONYMOUS` is intentional: Azure Bot Service calls the
messaging endpoint without an Azure Functions key. Authenticity of
inbound activities is instead verified inside the Microsoft Teams SDK's
FastAPIAdapter using CLIENT_ID / CLIENT_SECRET / TENANT_ID — the same
security model the original uvicorn-hosted app relied on.
"""
import azure.functions as func

from main import api

app = func.AsgiFunctionApp(app=api, http_auth_level=func.AuthLevel.ANONYMOUS)
