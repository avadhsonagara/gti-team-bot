# Azure Container — GTI Teams Bot & RS Alerts

This folder holds two apps that talk to Google Threat Intelligence (GTI) and post into
Microsoft Teams. They're grouped here because they were pulled out of the `azure/`
Functions tree for separate iteration — **not** because both run as containers:

| | Runtime | What it does |
|---|---|---|
| [`azure-bot-container/`](azure-bot-container) | **Azure Container Apps** (FastAPI/uvicorn) | Interactive bot — answers threat-intel questions asked in Teams |
| [`rs-alerts/`](rs-alerts) | **Azure Functions** (Timer Trigger) — unchanged | Background job — pushes new GTI alerts into a Teams channel on a schedule |

`rs-alerts` is still a Functions app; it lives here only because it was copied alongside
the bot, not because it was ported to Container Apps. Don't assume the folder name implies
otherwise.

Both are near-duplicates of apps that also exist under [`../azure/`](../azure) (and, for
the underlying business logic, [`../gcp/`](../gcp)) — there is no shared library between
these copies. If you fix something here, check whether the same fix is needed in the
`azure/` (and `gcp/`) counterpart, and vice versa.

## Deploy to Azure

[`bicep/`](bicep) provisions both apps in one deployment:

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Favadhsonagara%2Fgti-team-bot%2Fmain%2Fazure-container%2Fbicep%2Fazuredeploy-button.json)

- **The bot** — a Container Apps Environment with a **Dedicated workload profile carrying
  ingress** (this is what "Premium ingress" means in Container Apps terms — it's what
  unlocks a configurable idle-request-timeout past the Consumption-only default), and the
  Container App itself, pulling a pre-built image from a **public Docker Hub repository**
  (`avadhsonagara/gti-teams-bot-agentic` by default — see `botDockerHubRepository`). The
  image is built and pushed by hand, not by this template:

  ```bash
  cd azure-container/azure-bot-container
  docker build -t <your-dockerhub-user>/gti-teams-bot-agentic:latest .
  docker push <your-dockerhub-user>/gti-teams-bot-agentic:latest
  ```

  Rebuild and push again whenever the bot's code or dependencies change, then redeploy (or
  bump `botImageTag` and redeploy) to pick up the new image. The repo must stay **public** —
  this template configures no registry credentials for pulling it; wire a
  `configuration.registries` entry with a Docker Hub access token if you switch to private.
- **RS Alerts** — a Function App on **Consumption or Flex Consumption** only (no Premium
  option here). Same zip-deploy mechanism as `azure/infra`, pointed at this folder's
  [`rs-alerts/code.zip`](rs-alerts/code.zip) — this one still ships as a pre-built zip, not a
  container image.
- Shared: one storage account, one Key Vault (holding `GTI_API_KEY`), one User-Assigned
  Managed Identity (the bot's Azure Bot App ID — no client secret anywhere), and the Azure
  Bot resource itself, wired to the Container App's FQDN.

Every custom-resource property used in `bicep/main.bicep` (Container Apps ingress/workload
profiles, Key-Vault-backed Container Apps secrets) was checked against Microsoft's own ARM
template reference before being written, and the full template — every parameter
combination (Consumption/Flex Consumption, RS Alerts on/off, the deploy-button wrapper) —
was validated with `az deployment group validate` against a real subscription, not just
compiled. That validation pass is also what caught a real platform constraint no amount of
reading would have surfaced from the docs alone: a workload profile hosting ingress needs
**at least 2 nodes**, or Azure rejects the deployment outright
(`ManagedEnvironmentIngressConfigurationInvalidWorkloadProfile`) — reflected in
`dedicatedWorkloadProfileMinNodeCount`'s default and minimum.

You'll need:

- An Azure subscription and resource group with Contributor access.
- A Google Threat Intelligence (VirusTotal) API key.
- The bot image already built and pushed to a public Docker Hub repository (see above) —
  the deployment doesn't build it for you.
- (Optional, for RS Alerts) The target Teams channel's full link and your GTI project ID
  from the Alerts page URL (`...&project=projects/<id>`).

For full CLI control (custom naming, a different Dedicated SKU, reusing an existing
storage account, etc.), deploy `bicep/main.bicep` directly instead of the button:

```bash
az deployment group create \
  --resource-group <RG> \
  --template-file bicep/main.bicep \
  --parameters bicep/main.parameters.json \
  --parameters gtiApiKey=<your-gti-api-key>
```

After deployment: sideload the generated Teams app manifest (uploaded to the new storage
account's `teams-manifest` blob container — see the `manifestBlobUrl` output) and grant the
bot's managed identity the Microsoft Graph **application** permissions
`ChannelMessage.Read.All` (bot thread context) and, if RS Alerts is enabled,
`TeamsAppInstallation.ReadWriteForTeam.All` (auto-install) with tenant-admin consent —
same manual one-time steps as the `azure/infra` deployment.

**`rs-alerts/code.zip` is a static snapshot, not a live build** — rebuild and recommit it
whenever that app's code or dependencies change, or the button silently ships stale code
(the bot has no equivalent staleness risk here, since its Docker Hub image is rebuilt and
pushed independently of this template — see above):

```bash
cd azure-container/rs-alerts && rm -f code.zip && \
  git ls-files --cached --others --exclude-standard | grep -v '\.zip$' | zip -X code.zip -@
```

After changing `bicep/main.bicep` or `bicep/deploy-button.bicep`, recompile the checked-in
ARM JSON so they stay byte-for-byte in sync with the Bicep source:

```bash
cd azure-container/bicep
az bicep build --file main.bicep --outfile azuredeploy.json
az bicep build --file deploy-button.bicep --outfile azuredeploy-button.json
```

---

## `azure-bot-container` — the interactive bot

Container-hosted version of the bot in [`../azure/azure-bot-function`](../azure/azure-bot-function).
Same `app/` business logic, served by FastAPI/uvicorn instead of the Azure Functions host —
built because Azure Functions enforces a 230-second HTTP response ceiling on every hosting
plan (Consumption, Flex Consumption, Premium, Dedicated alike) that `host.json` cannot lift,
and this bot's GTI queries can take several minutes.

### Local development

```bash
cd azure-container/azure-bot-container
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in CLIENT_ID, MANAGED_IDENTITY_CLIENT_ID, GTI_API_KEY
uvicorn main:app --reload --port 8000
```

`GET /`, `GET /health`, and `POST /api/messages` behave exactly as they did in the
Functions version.

### Build the image

```bash
docker build -t gti-teams-bot-agentic:latest .
docker run --env-file .env -p 8000:8000 gti-teams-bot-agentic:latest
```

Push it to your Azure Container Registry before deploying:

```bash
az acr build --registry <ACR_NAME> --image gti-teams-bot-agentic:latest .
```

### Deploying to Azure Container Apps — the part that actually matters

The default Container Apps ingress mode caps every HTTP request at **240 seconds**, full
stop, with no setting to raise it — the same wall Azure Functions has, just a few seconds
wider. If any GTI query can take longer than that (this bot's client-side timeout defaults
to 600s, see `.env.example`), you **must** configure the environment for **Premium ingress
mode** and explicitly raise its idle request timeout, or every slow query will be cut off by
the platform regardless of anything in this image:

```bash
# Premium ingress requires a dedicated workload profile (not Consumption) —
# this is a real infra/cost decision, not a toggle.
az containerapp env update \
  --name <ENV_NAME> --resource-group <RG> \
  --ingress-mode premium

az containerapp env premium-ingress update \
  --name <ENV_NAME> --resource-group <RG> \
  --idle-timeout 15   # minutes — comfortably covers GTI_TIMEOUT_SECONDS=600s:
                       # a read timeout (GTI accepted the query but took too
                       # long to answer) is NOT retried (app/gti/client.py) —
                       # it fails fast with one wait instead of compounding
                       # across retries, since GTI may already be mid-
                       # processing and a retry risks a duplicate session.
                       # Only connect/write/pool timeouts and 5xx/429 are
                       # retried, and those resolve quickly; max is 30.
```

When creating the Container App itself:

- **Target port must be `8000`** (what this image's uvicorn listens on):
  `--target-port 8000 --ingress external`.
- **Attach the same User-Assigned Managed Identity** the Functions version used
  (`--user-assigned <IDENTITY_RESOURCE_ID>`), and set
  `CLIENT_ID`/`MANAGED_IDENTITY_CLIENT_ID` to its client ID — `app/teams/bot_client.py`
  and `app/graph/client.py` need no code changes, Managed Identity auth works the same way
  on Container Apps as it did on Functions.
- **Scale by replicas, not by uvicorn workers.** Don't add `--workers N` to the Dockerfile's
  `CMD` — scale horizontally instead with `--min-replicas`/`--max-replicas` and an HTTP
  scale rule:

  ```bash
  az containerapp create \
    --name <APP_NAME> --resource-group <RG> --environment <ENV_NAME> \
    --image <ACR_NAME>.azurecr.io/gti-teams-bot-agentic:latest \
    --target-port 8000 --ingress external \
    --min-replicas 1 --max-replicas 10 \
    --scale-rule-name http-rule --scale-rule-type http \
    --scale-rule-http-concurrency 40
  ```

  **This is not the same guarantee as Cloud Run's `--concurrency`.** On Cloud Run,
  `--concurrency` is a hard cap the platform's own routing layer enforces — it will never
  hand a single instance more than N concurrent requests. On Container Apps,
  `concurrentRequests` / `--scale-rule-http-concurrency` is a **scale-out trigger
  threshold**: KEDA checks it every 30 seconds and adds a replica once it's exceeded, but
  Microsoft's own docs list "replica quantities are a target amount, not a guarantee" as a
  known limitation. A traffic burst can land more than N concurrent requests on one replica
  for up to that ~30s polling window before another replica comes up. This app has no
  in-process concurrency cap to cushion that (deliberately — see `main.py`), so if that gap
  matters for your GTI rate limits or the container's own resource headroom, keep
  `--min-replicas` at 2+ so there's always spare capacity rather than scaling from zero, and
  pick a conservative `--scale-rule-http-concurrency` (well under what one replica can
  actually sustain, not the number you want it to sustain on average).
- **Point the Azure Bot resource's messaging endpoint** at this Container App's FQDN
  (`https://<app>.<env-suffix>.azurecontainerapps.io/api/messages`) instead of the Function
  App's — the Teams app manifest itself doesn't need to change.
- **Configure a liveness/readiness probe against `/health` on the Container App resource.**
  This is a platform-level setting, not something the image itself provides — without it
  configured, Container Apps has no way to detect and recycle a replica whose process is
  wedged.

### What's different from `azure-bot-function`

| | Functions | Container Apps |
|---|---|---|
| Entry point | `function_app.py` (Azure Functions host) | `main.py` (FastAPI/uvicorn) |
| HTTP timeout ceiling | 230s, fixed, every plan | Configurable up to 30 min (Premium ingress) |
| Execution model | Synchronous `requests`, one thread per invocation | Fully async (`httpx`, `azure-*.aio`) — no worker threads |
| Concurrency control | `httpPerInstanceConcurrency` / `pythonThreadPoolCount` (host config) | Container Apps HTTP scale rule (platform-level, not in-process — see above) |
| GTI client timeout | hardcoded `240.0` (bug) | `GTI_TIMEOUT_SECONDS`, default `600` |

`app/` is functionally equivalent to `azure-bot-function`'s, but every I/O call was
converted from synchronous `requests`/`azure-identity`/`azure-storage-blob`/`azure-data-tables`
to their async counterparts (`httpx.AsyncClient`, `azure.identity.aio`,
`azure.storage.blob.aio`, `azure.data.tables.aio`) so a multi-minute GTI call never blocks
the event loop from serving other concurrent requests. If you change business logic in one
variant, port it to the other by hand — including re-adding the `await`s if you're porting
logic *from* the Functions version.

---

## `rs-alerts` — the background alert job

A background **Azure Function** (Timer Trigger, unchanged from its `azure/rs-alerts`
counterpart) that fetches Google Threat Intelligence alerts incrementally and delivers them
as Adaptive Cards into a Microsoft Teams channel — ported from the standalone
[`gti-alerts`](../../gti-team-bot/gti-alerts) Google Chat script.

```
GTI List Alerts API ──► rs_alerts_timer (Timer Trigger) ──► Bot Framework Connector API ──► Teams Channel
```

### How it differs from the original `gti-alerts` script

| | `gti-team-bot/gti-alerts` (Google Chat) | `rs-alerts` (this app, Teams) |
|---|---|---|
| Host | Google Cloud Function (Gen2) + Cloud Scheduler | Azure Function, Timer Trigger |
| Cursor state | Local `state.json` file | Blob in the Function App's own storage account (`app/state_store.py`) — local disk isn't durable across Flex Consumption timer ticks |
| Bot auth | Google ADC / service account, scoped to `chat.bot` | User-Assigned Managed Identity (`MANAGED_IDENTITY_CLIENT_ID`) only — no client secret anywhere (`app/bot_auth.py`) |
| Manual run | `python3 gti_alerts.py` | `GET`/`POST` to `/api/trigger` (function-key protected), or `func start` locally |
| Cursor filter | Strict `audit.update_time > cursor` | Inclusive `>=`, plus a per-timestamp sent-id set in the cursor blob — a strict `>` can permanently drop an alert that ties on `audit.update_time` with another one if a run is interrupted between them; deliberately kept even though it diverges from the reference script, which has this same gap |

Backfill window (`BACKFILL_DAYS`), required-non-empty level filters, and the card's
detail-type-specific fields (IAB/Data Leak/Insider Threat severity, vulnerability match
details) all match the reference script's intent, adapted to GTI's actual API response
shapes. Delivery is deliberately **not** batched, unlike the reference script's Google Chat
batching — each alert goes out as its own Teams message, one Adaptive Card each,
checkpointed individually (`app/sender.py`). The delivery target and card schema obviously
don't match either — Adaptive Cards via the Bot Framework Connector API instead of Google
Chat Cards v2 (`app/cards.py`, `app/sender.py`).

### Configuration

See [`rs-alerts/.env.example`](rs-alerts/.env.example) for the full list of environment
variables. For local development with `func start`, put the same values into
`local.settings.json`'s `Values` object instead (Azure Functions Core Tools doesn't read
`.env`).

| Variable | Required | Description |
|---|---|---|
| `TEAMS_CHANNEL_ID` | ✅ | Teams channel link or ID (`19:xxx@thread.tacv2`) |
| `GTI_API_KEY` | ✅ | GTI API key |
| `GTI_RSA_PROJECT` | ✅ | GTI project id, from the Alerts URL `...&project=projects/<id>` |
| `CLIENT_ID` / `MANAGED_IDENTITY_CLIENT_ID` | ✅ | Bot's User-Assigned Managed Identity client ID — the only credential this job authenticates with |
| `RS_ALERTS_SCHEDULE` | ❌ | NCRONTAB schedule. Default: every 15 minutes (`0 */15 * * * *`) |
| `BACKFILL_DAYS` | ❌ | Backfill window (days) used to seed the cursor on the very first run. Default: 7, clamped to 1-7 |
| `FILTER_*` | ❌ | Severity/priority/relevance/confidence filters — each must resolve to at least one value; see `.env.example` |

The bot must already be **installed in the target Teams channel**, otherwise the Bot
Framework Connector API returns 404 when posting.

### Running locally

This job authenticates exclusively via User-Assigned Managed Identity —
`ManagedIdentityCredential` only resolves against Azure's instance metadata service, which
doesn't exist outside Azure, so `func start` on a laptop cannot acquire a Bot Framework/Graph
token on its own. To iterate on code changes, deploy to a real (dev/staging)
`<functionAppName>-rs-alerts` Function App — its Managed Identity makes outbound auth work
immediately:

```bash
func azure functionapp publish <functionAppName>-rs-alerts --python
```

Trigger a run without waiting for the timer:

```bash
curl "https://<functionAppName>-rs-alerts.azurewebsites.net/api/trigger?code=<function-key>"
```

### Deploying

This variant of `rs-alerts` is not currently wired into any Bicep template of its own —
provisioning follows the same path as [`../azure/rs-alerts`](../azure/rs-alerts): infra is
provisioned by [`../azure/infra/main.bicep`](../azure/infra/main.bicep) when
`enableRsAlerts=true` (see the repo-level [Deploy to Azure button](../README.md#deploy-to-azure)),
which zip-deploys `azure/rs-alerts`'s code, not this folder's. If you intend to run *this*
copy in production, you'll need to point that deployment at this folder's code instead
(e.g. rebuild `rs-alerts/code.zip` from here and update the template's zip URL) — as things
stand, changes made only here don't reach a real deployment on their own.

```bash
func azure functionapp publish <functionAppName>-rs-alerts --python
```
