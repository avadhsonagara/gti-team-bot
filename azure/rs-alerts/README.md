# RS Alerts — GTI Alerts → Microsoft Teams (Azure Functions)

A background Azure Function that fetches **Google Threat Intelligence (GTI) alerts**
incrementally and delivers them as **Adaptive Cards** into a Microsoft Teams channel —
ported from the standalone [`gti-alerts`](../../../gti-team-bot/gti-alerts) script to run
natively on Azure Functions (Python v2 programming model), Timer Trigger.

```
GTI List Alerts API ──► rs_alerts_timer (Timer Trigger) ──► Bot Framework Connector API ──► Teams Channel
```

This is provisioned as an **optional** part of the main deployment: the "Deploy to Azure"
button and `infra/deploy.sh` expose an `enableRsAlerts` toggle. When enabled, the template
provisions a second, dedicated Function App (`<functionAppName>-rs-alerts`) alongside the
main bot, sharing its Azure Bot identity, Key Vault secret, and storage account — see
[`../infra/main.bicep`](../infra/main.bicep).

## How it differs from the original `gti-alerts` script

| | `gti-team-bot/gti-alerts` (GCP) | `rs-alerts` (this app) |
|---|---|---|
| Host | Google Cloud Function (Gen2) + Cloud Scheduler | Azure Function, Timer Trigger |
| Cursor state | Local `state.json` file | Blob in the Function App's own storage account (`app/state_store.py`) — local disk isn't durable across Flex Consumption timer ticks |
| Bot auth | `CLIENT_ID` + `CLIENT_SECRET` client-credentials grant | User-Assigned Managed Identity (`MANAGED_IDENTITY_CLIENT_ID`) when deployed via `main.bicep`, matching the bot's `UserAssignedMSI` registration — falls back to `CLIENT_SECRET` for local dev (`app/bot_auth.py`) |
| Manual run | `python3 gti_alerts.py` | `GET`/`POST` to `/api/trigger` (function-key protected), or `func start` locally |

The alert-fetching, filtering, and Adaptive Card logic (`app/gti_client.py`, `app/cards.py`)
is otherwise unchanged from the source script.

## Configuration

See [`.env.example`](.env.example) for the full list of environment variables. For local
development with `func start`, put the same values into `local.settings.json`'s `Values`
object instead (Azure Functions Core Tools doesn't read `.env`).

| Variable | Required | Description |
|---|---|---|
| `TEAMS_CHANNEL_ID` | ✅ | Teams channel link or ID (`19:xxx@thread.tacv2`) |
| `GTI_API_KEY` | ✅ | GTI API key |
| `GTI_RSA_PROJECT` | ✅ | GTI project id, from the Alerts URL `...&project=projects/<id>` |
| `MANAGED_IDENTITY_CLIENT_ID` | one of these two | Bot's managed identity client ID (Azure deployments) |
| `CLIENT_ID` / `CLIENT_SECRET` / `TENANT_ID` | one of these two | Classic app registration credentials (local dev) |
| `RS_ALERTS_SCHEDULE` | ❌ | NCRONTAB schedule. Default: every 15 minutes (`0 */15 * * * *`) |
| `BACKFILL_DAYS` | ❌ | Days to backfill on first run. Default: `7` |
| `FILTER_*` | ❌ | Severity/priority/relevance/confidence filters — see `.env.example` |

The bot must already be **installed in the target Teams channel**, otherwise the Bot
Framework Connector API returns 404 when posting.

## Running locally

```bash
cd azure/rs-alerts
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example local.settings.json.values   # then paste into local.settings.json
func start
```

Trigger a run without waiting for the timer:

```bash
curl "http://localhost:7071/api/trigger"
```

## Deploying

Infra is provisioned by `../infra/main.bicep` when `enableRsAlerts=true` (see the repo-level
[Deploy to Azure button](../../README.md) and [`../infra/deploy.sh`](../infra/deploy.sh)).
That provisions the Function App, its storage container, and wires up the shared managed
identity / Key Vault secret — it does not push application code. Publish this folder's code
separately:

```bash
func azure functionapp publish <functionAppName>-rs-alerts --python
```
