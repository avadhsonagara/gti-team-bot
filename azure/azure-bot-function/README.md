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
  Managed-Identity-only: a **User-Assigned Managed Identity**
  (`MANAGED_IDENTITY_CLIENT_ID`) provisioned by `azure/infra/main.bicep` —
  no client secret exists anywhere in this codebase.
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
| `CLIENT_ID` | Microsoft App ID — the bot's User-Assigned Managed Identity client ID (same value as `MANAGED_IDENTITY_CLIENT_ID`) |
| `MANAGED_IDENTITY_CLIENT_ID` | The User-Assigned Managed Identity's client ID — the only credential this bot authenticates with |
| `GTI_API_KEY` | Google Threat Intelligence / VirusTotal API Key |
| `AzureWebJobsStorage` | Storage account connection string (session + output-format persistence) |

---

## Local Development

This bot authenticates exclusively via User-Assigned Managed Identity —
there is no client-secret fallback, so `func start` on a laptop cannot
acquire a Bot Framework/Graph token on its own (`ManagedIdentityCredential`
only resolves against Azure's instance metadata service, which doesn't
exist outside Azure). To iterate on code changes:

1. Deploy to a real (dev/staging) Function App provisioned by
   `azure/infra/main.bicep` — its Managed Identity makes outbound auth work
   immediately, no local credentials needed.
2. For fast inner-loop iteration, use `func azure functionapp publish
   <dev-function-app-name>` against that dev app, or attach VS Code's Azure
   Functions remote debugger to it.
3. Point your Azure Bot registration's messaging endpoint at that dev
   Function App's `/api/messages` URL while iterating.

Code paths that don't need outbound Bot Framework/Graph auth (e.g. request
parsing in `app/teams/activity.py`) can still be unit-tested locally without
any of this.

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

# Assign the User-Assigned Managed Identity this bot authenticates with —
# see azure/infra/main.bicep for provisioning it and granting it the Bot
# Framework / Graph (ChannelMessage.Read.All) permissions it needs.
az functionapp identity assign \
  --resource-group <rg> --name <function-app-name> \
  --identities <managed-identity-resource-id>

# Configure app settings (equivalent of local.settings.json "Values")
az functionapp config appsettings set \
  --resource-group <rg> --name <function-app-name> \
  --settings \
    CLIENT_ID=<managed-identity-client-id> \
    MANAGED_IDENTITY_CLIENT_ID=<managed-identity-client-id> \
    GTI_API_KEY=<gti-api-key>

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

---

## Microsoft Graph permissions

This bot's identity needs three Microsoft Graph **APPLICATION** permissions
beyond its Bot Framework ones — `ChannelMessage.Read.All` (channel thread
context, `app/teams/thread.py`), `Chat.Read.All` (the group-chat equivalent
of that same lookup, for file attachments shared in group chats), and
`Files.Read.All` (downloading the actual file bytes via the Graph Shares
API, `app/teams/attachments.py`). ARM/Bicep has no native resource type for
granting a Graph app role, so this is a manual, one-time step per bot,
after deployment: Entra admin center → *Enterprise applications* → this
bot's identity → *Permissions* → *Add a permission* → *Microsoft Graph* →
*Application permissions* → select all three above → *Grant admin consent*.
Requires a Global Administrator or Privileged Role Administrator to do it.
