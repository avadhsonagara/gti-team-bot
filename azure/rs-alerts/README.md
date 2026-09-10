# RS Alerts — GTI Alerts → Microsoft Teams (Azure Functions)

A background Azure Function that fetches **Google Threat Intelligence (GTI) alerts**
incrementally and delivers them as **Adaptive Cards** into a Microsoft Teams channel —
ported from the standalone [`gti-alerts`](../../../gti-team-bot/gti-alerts) script to run
natively on Azure Functions (Python v2 programming model), Timer Trigger.

```
GTI List Alerts API ──► rs_alerts_timer (Timer Trigger) ──► Bot Framework Connector API ──► Teams Channel
```

This is provisioned as an **optional** part of the main deployment: the "Deploy to Azure"
button and a direct `az deployment group create` against `infra/main.bicep` both expose an
`enableRsAlerts` toggle. When enabled, the template
provisions a second, dedicated Function App (`<functionAppName>-rs-alerts`) alongside the
main bot, sharing its Azure Bot identity, Key Vault secret, and storage account — see
[`../infra/main.bicep`](../infra/main.bicep).

## How it differs from the original `gti-alerts` script

| | `gti-team-bot/gti-alerts` (GCP) | `rs-alerts` (this app) |
|---|---|---|
| Host | Google Cloud Function (Gen2) + Cloud Scheduler | Azure Function, Timer Trigger |
| Cursor state | Local `state.json` file | Blob in the Function App's own storage account (`app/state_store.py`) — local disk isn't durable across Flex Consumption timer ticks |
| Bot auth | `CLIENT_ID` + `CLIENT_SECRET` client-credentials grant | User-Assigned Managed Identity (`MANAGED_IDENTITY_CLIENT_ID`) only, provisioned by `main.bicep`, matching the bot's `UserAssignedMSI` registration — no client secret anywhere (`app/bot_auth.py`) |
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
| `CLIENT_ID` / `MANAGED_IDENTITY_CLIENT_ID` | ✅ | Bot's User-Assigned Managed Identity client ID — the only credential this job authenticates with |
| `RS_ALERTS_SCHEDULE` | ❌ | NCRONTAB schedule. Default: every 15 minutes (`0 */15 * * * *`) |
| `FILTER_*` | ❌ | Severity/priority/relevance/confidence filters — see `.env.example` |

The bot must already be **installed in the target Teams channel**, otherwise the Bot
Framework Connector API returns 404 when posting.

## Running locally

This job authenticates exclusively via User-Assigned Managed Identity —
`ManagedIdentityCredential` only resolves against Azure's instance metadata
service, which doesn't exist outside Azure, so `func start` on a laptop
cannot acquire a Bot Framework/Graph token on its own. To iterate on code
changes, deploy to a real (dev/staging) `<functionAppName>-rs-alerts`
Function App provisioned by `../infra/main.bicep` — its Managed Identity
makes outbound auth work immediately:

```bash
func azure functionapp publish <functionAppName>-rs-alerts --python
```

Trigger a run without waiting for the timer:

```bash
curl "https://<functionAppName>-rs-alerts.azurewebsites.net/api/trigger?code=<function-key>"
```

## Deploying

Infra is provisioned by `../infra/main.bicep` when `enableRsAlerts=true` (see the repo-level
[Deploy to Azure button](../../README.md), or deploy the template directly with
`az deployment group create --template-file ../infra/main.bicep --parameters
../infra/main.parameters.json --parameters enableRsAlerts=true
rsAlertsTeamsChannelId=<link> rsAlertsGtiProject=<id>`). That provisions the Function App, its
storage container, wires up the shared managed identity / Key Vault secret, **and (unless
`rsAlertsCodeZipUrl` is cleared) zip-deploys this folder's code into it automatically** from
the pre-built [`code.zip`](code.zip) committed alongside it — no separate publish step needed.
**Rebuild and commit `code.zip` whenever this folder's code or dependencies change** (see the
root [README](../../README.md#deploy-to-azure) for the rebuild command). The manual publish
below is only for iterating against an already-provisioned Function App without re-running the
Bicep template:

```bash
func azure functionapp publish <functionAppName>-rs-alerts --python
```
