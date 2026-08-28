# GTI Teams Bot (Agentic) — Azure Functions

This is the [gti-team-bot-agentic](../gti-team-bot-agentic) FastAPI app converted to run natively
on **Azure Functions** (Python v2 programming model), instead of Uvicorn / Docker.

The application code itself — `app/` (config, GTI client, Teams handlers, Adaptive Cards, logging)
and `main.py` (the FastAPI app + Bot Framework wiring) — is **unchanged** from the source project.
Only the hosting layer is different: `function_app.py` wraps the existing FastAPI app as a single
Azure Function using `azure.functions.AsgiFunctionApp`, so all the original routes keep working
exactly as before:

| Route | Purpose |
|---|---|
| `GET /` | Root info / liveness |
| `GET /health` | Liveness probe |
| `GET /api/messages` | Informational GET handler |
| `OPTIONS /api/messages` | CORS/preflight handler |
| `POST /api/messages` | Bot Framework messaging endpoint (Teams SDK `FastAPIAdapter`) |

`host.json` sets `extensions.http.routePrefix` to `""` so these paths are exposed as-is, without
Azure's default `/api` prefix — this keeps the Bot messaging endpoint at `/api/messages`, matching
the Azure Bot resource configuration and `teams-app-manifest/manifest.json` from the source project.

---

## How the FastAPI app is wired into Azure Functions

`function_app.py`:

```python
import azure.functions as func
from main import api

app = func.AsgiFunctionApp(app=api, http_auth_level=func.AuthLevel.ANONYMOUS)
```

`AsgiFunctionApp` registers one HTTP-triggered function bound to route `{*route}` covering every
HTTP method, and on the **first** invocation it drives the ASGI lifespan protocol
(`AsgiMiddleware.notify_startup()`). That runs `main.py`'s `lifespan()` context manager — the same
`await teams_app.initialize()` / `await gti_client.close()` path used when running under Uvicorn —
so no extra startup code was needed.

`http_auth_level=ANONYMOUS` is intentional: Azure Bot Service calls the messaging endpoint without
an Azure Functions key. Authenticity of inbound activities is verified inside the Microsoft Teams
SDK's `FastAPIAdapter` itself, using `CLIENT_ID` / `CLIENT_SECRET` / `TENANT_ID` — the same security
model the original Uvicorn-hosted app relied on.

---

## Prerequisites

* **Python 3.11+ or 3.12**
* [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
* An Azure subscription with a **Function App** (Linux, Python, Consumption/Premium/Dedicated plan)
* An **Azure Bot** registration (Single Tenant or Multi-Tenant) — same as the source project
* A **Google Threat Intelligence (VirusTotal)** API key with Threat Intelligence access

---

## Local Development

```bash
cd gti-team-bot-agentic-azure
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Fill in secrets in `local.settings.json` → `Values` (Azure Functions Core Tools reads this file
instead of `.env`):

```json
{
  "Values": {
    "CLIENT_ID": "...",
    "CLIENT_SECRET": "...",
    "TENANT_ID": "...",
    "GTI_API_KEY": "..."
  }
}
```

Run locally:

```bash
func start
```

The bot will listen on `http://localhost:7071/api/messages` (and `/`, `/health`). To receive real
Teams/Bot Framework traffic locally, tunnel it with `ngrok` / `devtunnel` and point the Azure Bot's
messaging endpoint at the tunnel URL, same as you would for the Uvicorn version.

---

## Deploy to Azure

```bash
# One-time: create the Function App (Linux, Python 3.12, Consumption or Premium plan)
az functionapp create \
  --resource-group <rg> \
  --name <function-app-name> \
  --storage-account <storage-account> \
  --consumption-plan-location <region> \
  --runtime python --runtime-version 3.12 \
  --functions-version 4 \
  --os-type linux

# Configure app settings (equivalent of local.settings.json "Values")
az functionapp config appsettings set \
  --resource-group <rg> --name <function-app-name> \
  --settings \
    CLIENT_ID=<entra-app-client-id> \
    CLIENT_SECRET=<entra-app-client-secret> \
    TENANT_ID=<entra-tenant-id> \
    GTI_API_KEY=<gti-api-key> \
    GTI_API_BASE_URL=https://www.virustotal.com/api/v3 \
    GTI_TIMEOUT_SECONDS=180.0 \
    GTI_MAX_RETRIES=2 \
    GTI_RETRY_DELAY=2.0 \
    LOG_FORMAT=text \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true

# Deploy the code (from this directory)
func azure functionapp publish <function-app-name>
```

Then, in the **Azure Bot** resource's *Configuration* blade, set the messaging endpoint to:

```
https://<function-app-name>.azurewebsites.net/api/messages
```

Sideload `teams-app-manifest/` from the source project into Teams exactly as documented there
(`botId` must match `CLIENT_ID`).

---

## Important Operational Notes

* **Request duration vs. plan limits** — Each incoming Teams message is handled synchronously for
  the full GTI round trip (`GTI_TIMEOUT_SECONDS=180` by default, times up to `GTI_MAX_RETRIES + 1`
  attempts with backoff). `host.json` sets `functionTimeout` to `00:10:00`, the maximum configurable
  on the **Consumption** plan. If you see timeouts under load or with retries, either move to an
  **Elastic Premium** or **Dedicated (App Service)** plan — where `functionTimeout` can be raised
  further or set to `-1` (unbounded) — or tune down `GTI_TIMEOUT_SECONDS` / `GTI_MAX_RETRIES`.
* **Cold starts** — On the Consumption plan, an idle Function App can cold-start on the next Teams
  message, adding latency before the bot posts its "⏳ Looking into that…" placeholder. If that's
  noticeable, use a Premium plan with *Always Ready Instances*, or enable *Always On* on a Dedicated
  plan.
* **Dependencies trimmed for this host**: `uvicorn` was dropped from `requirements.txt` (Azure
  Functions provides its own HTTP listener/worker — Uvicorn is never invoked). The source project's
  optional `google-cloud-firestore` dependency was also dropped since it isn't imported anywhere in
  `app/`; add it back if you wire up cross-instance session persistence later.
* **Secrets** — `local.settings.json` is git-ignored (contains secrets locally). In Azure, configure
  the same values as Function App **Application Settings** (or pull from Key Vault via
  `@Microsoft.KeyVault(...)` references) rather than committing them anywhere.

---

## Verification

* **Root Info**: `GET https://<function-app>.azurewebsites.net/`
* **Liveness Probe**: `GET https://<function-app>.azurewebsites.net/health`
* **Bot Webhook**: `POST https://<function-app>.azurewebsites.net/api/messages`

Usage in Teams is identical to the source project — see its `README.md` for example queries
(`@GTI Agent What is the reputation of IP 1.1.1.1?`, etc.).
