# GTI Teams Bot (Agentic) — Azure Functions (no SDK)

This is the **Google Threat Intelligence (GTI) Microsoft Teams Agentic Bot**,
packaged as an **Azure Function (Python v2 programming model)**, replacing
the previous SDK-based bot implementation.
This version has **no dependency on the `microsoft-teams-apps` SDK at all**, and **no `async`/
`await` anywhere in the codebase** — everything talks to the Bot Framework
REST APIs directly with plain `requests`, the same style already used by
[`../rs-alerts`](../rs-alerts).

---

## Why no SDK

`../../gcp/gcp-bot-function` (this bot's GCP sibling) went through the same
conversion first, and hit a real bug doing it: the `microsoft-teams-apps`
SDK is async-only internally, and its own HTTP client / token manager bind
their async primitives to whichever event loop touches them first. That's
fine under a persistent server (uvicorn, or Azure's own ASGI bridge), but
broke under a naive "one event loop per request" model. Rather than carry
that fragility into a new host, this version hand-rolls the small set of
things the SDK was actually doing:

- `app/teams/auth.py` — verifies the inbound JWT (RS256 against Bot
  Framework's public JWKS: issuer, audience, expiry, and a `serviceUrl`
  claim match) — this logic is identical to the GCP sibling's, since Bot
  Framework's inbound authentication protocol has nothing to do with which
  cloud hosts the bot.
- `app/teams/activity.py` — parses the raw Activity JSON into the same
  attribute shape the SDK's typed model used to provide, so
  `handlers.py`/`attachments.py`/`thread.py` needed no logic changes.
- `app/teams/bot_client.py` — the bot's own Connector API token and
  sending/editing/deleting a Teams message. Token acquisition is
  Azure-specific: a **User-Assigned Managed Identity** in production
  (`MANAGED_IDENTITY_CLIENT_ID`, no secret involved), falling back to an
  Entra ID client-secret app registration for local dev — the same
  dual-mode pattern already proven in
  [`../rs-alerts/app/bot_auth.py`](../rs-alerts/app/bot_auth.py).
- `app/teams/context.py` — a small stand-in for the SDK's `ctx` object.

Everything else — the GTI client, Graph client, Table/Blob storage, and
thread-context fetching — is plain synchronous `def`s (Azure's Table/Blob Storage clients were never async
to begin with; only the SDK-dependent request/response layer needed
rewriting).

---

## Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/` | `GET` | Service liveness & metadata |
| `/health` | `GET` | Health check probe |
| `/api/messages` | `POST` | Microsoft Teams Bot Framework webhook endpoint |
| `/api/messages` | `OPTIONS` | CORS / preflight handler |
| `/api/messages` | `GET` | Informational status endpoint |

`host.json` sets `extensions.http.routePrefix` to `""` so these are exposed
exactly as listed, without Azure's default `/api` prefix — this keeps the
Bot messaging endpoint at `/api/messages`, matching the Azure Bot resource
configuration and `teams-app-manifest/manifest.json`.

`auth_level=ANONYMOUS` on every route is intentional: Azure Bot Service
calls the messaging endpoint without an Azure Functions key. Authenticity
is verified inside `app/teams/auth.py` using the bot's own App ID
(`CLIENT_ID`) instead.

---

## Configuration

See `.env.example` for the full list with defaults. The required ones:

| Variable | Description |
|---|---|
| `CLIENT_ID` | Microsoft App ID (Client ID) — Azure Bot registration / Managed Identity |
| `CLIENT_SECRET` | Local-dev fallback only — production uses `MANAGED_IDENTITY_CLIENT_ID` |
| `TENANT_ID` | Microsoft Entra Tenant ID |
| `GTI_API_KEY` | Google Threat Intelligence / VirusTotal API Key |
| `AzureWebJobsStorage` | Storage account connection string (session + output-format persistence) |

---

## Local Development

1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Put your secrets into `local.settings.json` → `Values` (Azure Functions
   Core Tools reads this file, not `.env`):
   ```json
   {
     "IsEncrypted": false,
     "Values": {
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "CLIENT_ID": "...",
       "CLIENT_SECRET": "...",
       "TENANT_ID": "...",
       "GTI_API_KEY": "..."
     }
   }
   ```

3. Run locally:
   ```bash
   func start
   ```
   The bot listens on `http://localhost:7071/api/messages` (and `/`, `/health`).

4. Expose locally via ngrok or a dev tunnel, and point your Azure Bot
   registration's messaging endpoint at `https://<tunnel>/api/messages`.

---

## Deploying

```bash
# One-time: create the Function App (Linux, Python, Consumption/Premium plan)
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
    CLIENT_ID=<entra-app-client-id-or-managed-identity-client-id> \
    TENANT_ID=<entra-tenant-id> \
    GTI_API_KEY=<gti-api-key>
    # Production: also set MANAGED_IDENTITY_CLIENT_ID and grant that
    # identity the Bot Framework / Graph permissions instead of CLIENT_SECRET.

# Deploy the code (from this directory)
func azure functionapp publish <function-app-name>
```

Then, in the **Azure Bot** resource's *Configuration* blade, set the
messaging endpoint to:
```
https://<function-app-name>.azurewebsites.net/api/messages
```

(See `../infra/main.bicep` for a full provisioning template —
Managed Identity, Storage Account, Key Vault, Application Insights;
this function can reuse the same infrastructure by pointing it at this
directory's code.)
