# GTI Teams Bot (Agentic)

A **Google Threat Intelligence (GTI)** agentic assistant for Microsoft Teams,
plus **RS Alerts**, a background job that posts new GTI alerts into a Teams
channel. Both ship as two parallel, independently deployable implementations:

| | Azure | GCP |
|---|---|---|
| Bot | [`azure/azure-bot-function`](azure/azure-bot-function) — Azure Functions | [`gcp/gcp-bot-function`](gcp/gcp-bot-function) — Cloud Run functions |
| RS Alerts | [`azure/rs-alerts`](azure/rs-alerts) | [`gcp/rs-alerts`](gcp/rs-alerts) |
| Infra | [`azure/infra`](azure/infra) — Bicep | [`gcp/infra`](gcp/infra) — Terraform |
| Docs | this file | [`gcp/README.md`](gcp/README.md) |

---

## Deploy to Azure

The button below provisions everything the bot needs — a Flex Consumption
Function App, a Key Vault holding your GTI API key, Application Insights, and
an Azure Bot resource wired to a User-Assigned Managed Identity (no app
registration or client secret to create by hand). RS Alerts is an optional
toggle on the same form.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Favadhsonagara%2Fgti-team-bot%2Fmain%2Fazure%2Finfra%2Fazuredeploy-button.json)

You'll need:

- An Azure subscription and resource group with Contributor access.
- A Google Threat Intelligence (VirusTotal) API key.
- (Optional, for RS Alerts) The target Teams channel's full link and your
  GTI project ID from the Alerts page URL (`...&project=projects/<id>`).

After deployment:

1. Sideload the generated Teams app manifest — the deployment uploads it to
   the new storage account's `teams-manifest` blob container (see the
   `manifestBlobUrl` output).
2. Grant the bot's managed identity the Microsoft Graph **application**
   permissions it needs for channel thread context and file attachments
   (`ChannelMessage.Read.All`, `Chat.Read.All`, `Files.Read.All`) — a manual,
   one-time admin-consent step with no ARM/Bicep equivalent. See
   [`azure/azure-bot-function/README.md`](azure/azure-bot-function/README.md#microsoft-graph-permissions).

Prefer full control over parameters (custom naming, reusing an existing
storage account or App Service Plan, etc.)? Deploy
[`azure/infra/main.bicep`](azure/infra/main.bicep) directly:

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file azure/infra/main.bicep \
  --parameters azure/infra/main.parameters.json \
  --parameters gtiApiKey=<your-gti-api-key>
```

---

## Repository layout

```
azure/                  Azure implementation (Functions + Bicep)
├── azure-bot-function/ Interactive Teams bot
├── rs-alerts/           GTI alerts → Teams channel background job
└── infra/               Bicep templates (main.bicep, deploy-button.bicep)
                          and their compiled ARM JSON + Deploy-to-Azure assets

gcp/                    GCP implementation (Cloud Run + Terraform) — see gcp/README.md
```

See each subproject's own README for configuration, local development, and
manual deployment steps.
