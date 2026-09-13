// =============================================================================
// GTI Teams Bot (Agentic) — Azure Container Apps + RS Alerts infrastructure
// =============================================================================
// Source: https://github.com/avadhsonagara/gti-team-bot
//
// This is the "azure-container" sibling of ../../azure/infra/main.bicep: the
// interactive bot runs as an Azure Container App instead of a Function App
// (see ../azure-bot-container/README.md for why — Azure Functions caps every
// HTTP response at 230s on every plan, and this bot's GTI queries can take
// several minutes). RS Alerts is UNCHANGED — it's still a Timer-Triggered
// Function App, provisioned the same way ../../azure/infra/main.bicep does
// it, just pointed at this folder's copy of the code.
//
// Provisions:
//   - A Container Apps Environment with a Dedicated workload profile carrying
//     the environment's ingress, so its idle-request-timeout can be raised
//     past the Consumption-only default (this is what "Premium ingress" means
//     in Container Apps terms — see the ingressConfiguration section below).
//   - The Container App itself, pulling a pre-built, publicly published image
//     from Docker Hub (see botDockerHubRepository below) — built and pushed
//     by hand (`docker build` + `docker push`), not built by this template.
//     Rebuild and push again whenever the bot's code or dependencies change.
//     Wired to the same User-Assigned Managed Identity pattern as the
//     Functions variant (Azure Bot's "UserAssignedMSI" app type — no client
//     secret).
//   - A Key Vault holding GTI_API_KEY, readable by that identity — and, for
//     the Container App specifically, referenced via Container Apps' own
//     Key-Vault-backed secrets mechanism (configuration.secrets), which is a
//     different mechanism from Function Apps' @Microsoft.KeyVault(...) app
//     setting syntax.
//   - RS Alerts: a Consumption or Flex Consumption Function App (Premium is
//     deliberately not offered here — see rsAlertsHostingPlanType below),
//     sharing the same storage account, Key Vault, and bot identity, with its
//     own code zip-deployed the same way ../../azure/infra/main.bicep does it.
//
// Two ways to deploy this template:
//   - bicep/deploy-button.bicep: a thin wrapper exposing only the fields a
//     first-time deployer actually needs, used by the "Deploy to Azure"
//     button.
//   - `az deployment group create --template-file bicep/main.bicep`: full CLI
//     control over every parameter below.
// =============================================================================

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

@description('Name of the Container App to create (the interactive bot).')
param containerAppName string = 'gti-teams-bot-agentic'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Tags applied to all resources.')
param tags object = {}

// ---------------------------------------------------------------------------
// Bot identity & secrets — shared by the Container App, RS Alerts, the Azure
// Bot resource, and Key Vault
// ---------------------------------------------------------------------------
// A User-Assigned (not System-Assigned) identity is required here: Azure
// Bot's "UserAssignedMSI" app type needs a stable client ID it can be
// configured with directly, and both the Container App and (if enabled) the
// RS Alerts Function App must run under that same identity so their own
// runtime code can request tokens under it (Bot Framework Connector API,
// Microsoft Graph). It also does double duty as the identity Container Apps
// uses to resolve the Key-Vault-backed GTI_API_KEY secret. No image-pull
// identity is needed at all — the bot's image is a public Docker Hub
// repository (see botDockerHubRepository below).

@description('Microsoft Entra tenant ID for the Azure Bot registration. Defaults to the deployment\'s own tenant.')
param tenantId string = subscription().tenantId

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting/env var.')
@secure()
param gtiApiKey string

@description('Globally-unique Key Vault name (3-24 characters) used to store gtiApiKey.')
@minLength(3)
@maxLength(24)
param keyVaultName string = toLower('kv-${take(replace(containerAppName, '-', ''), 8)}-${take(uniqueString(resourceGroup().id, subscription().id, containerAppName), 9)}')

@description('Globally-unique Storage Account name (3-24 lowercase alphanumeric characters). Backs the bot\'s output-format blob + GTI session table, and (if enabled) RS Alerts\' cursor blob and Flex Consumption deployment package.')
@minLength(3)
@maxLength(24)
param storageAccountName string = toLower('${take(replace(containerAppName, '-', ''), 11)}${uniqueString(resourceGroup().id, containerAppName)}')

// ---------------------------------------------------------------------------
// Bot — Docker Hub image
// ---------------------------------------------------------------------------
// The bot's image is built and pushed by hand — `docker build` + `docker
// push` from azure-bot-container/ — and published as a public Docker Hub
// repository, not built by this template. Bicep/ARM has no resource type for
// "build this Dockerfile", and this repo previously worked around that with
// a deploymentScript running `az acr build` against a pre-built code.zip;
// that path is no longer used for the bot (RS Alerts' Function App still
// zip-deploys the same way, unaffected). Rebuild and push again whenever the
// bot's code or dependencies change — this template has no way to detect
// that on its own.
//
// Public specifically because no secrets are baked into the image
// (GTI_API_KEY etc. are injected as Container App env/secrets at runtime,
// same as before) — so a public repo needs no registry credentials
// whatsoever for Container Apps to pull it, unlike a private one.

@description('Docker Hub repository the bot image is published to, as "namespace/repository" (no registry host prefix — Docker Hub is the default when none is given). Must be public: this template configures no registry credentials for pulling it.')
param botDockerHubRepository string = 'avadhsonagara/gti-teams-bot-agentic'

@description('Image tag to deploy. Bump this (or change forceUpdateTag) after pushing a new image to force the Container App to pick it up on redeploy.')
param botImageTag string = 'latest'

@description('Forces the Teams-manifest-upload deployment script to re-run on every deployment. Microsoft.Resources/deploymentScripts otherwise skips re-execution (and keeps its old environment variables) when redeployed without this changing.')
param forceUpdateTag string = utcNow()

// ---------------------------------------------------------------------------
// Bot — Container Apps Environment (Premium ingress)
// ---------------------------------------------------------------------------
// "Premium ingress" isn't a separate toggle in Container Apps — it's the
// result of moving the environment's ingress controller onto a Dedicated
// workload profile (workloadProfiles below) instead of the default
// Consumption profile, via ingressConfiguration.workloadProfileName. Only
// once ingress runs on a Dedicated profile can requestIdleTimeout be raised
// past the platform's fixed default — see azure-bot-container/README.md for
// why this bot needs that (its GTI queries can take several minutes).

@description('Name of the Container Apps Environment.')
param containerAppEnvName string = '${containerAppName}-env'

@description('Name of the Log Analytics workspace backing the Container Apps Environment (required dependency of every environment, regardless of ingress mode).')
param logAnalyticsWorkspaceName string = '${containerAppName}-logs'

@description('Dedicated workload profile SKU the environment\'s ingress (and the bot\'s own Container App) runs on, to unlock Premium ingress\'s configurable idle timeout. D-series are general-purpose (balanced CPU/memory); see https://learn.microsoft.com/azure/container-apps/workload-profiles-overview for the full size table.')
@allowed([
  'D4'
  'D8'
  'D16'
  'D32'
])
param dedicatedWorkloadProfileType string = 'D4'

@description('Minimum number of Dedicated-profile nodes always provisioned. Must be at least 2 — confirmed against a real deployment: Azure rejects a workload profile hosting ingress with fewer than 2 nodes (ManagedEnvironmentIngressConfigurationInvalidWorkloadProfile), and Dedicated profiles have no scale-to-zero regardless.')
@minValue(2)
@maxValue(20)
param dedicatedWorkloadProfileMinNodeCount int = 2

@description('Maximum number of Dedicated-profile nodes the environment can scale out to.')
@minValue(1)
@maxValue(20)
param dedicatedWorkloadProfileMaxNodeCount int = 3

@description('Idle request timeout (minutes) for Premium ingress — the platform will hold a connection open this long waiting for a response before cutting it off, regardless of anything the container itself does. Must comfortably exceed GTI_TIMEOUT_SECONDS. Valid range is 4-30 minutes (Azure Container Apps\' own hard ceiling); if GTI_TIMEOUT_SECONDS plus its retry backoff can exceed 30 minutes, lower GTI_TIMEOUT_SECONDS instead of expecting this value to cover it.')
@minValue(4)
@maxValue(30)
param premiumIngressIdleTimeoutMinutes int = 15

// ---------------------------------------------------------------------------
// Bot — Container App compute, scaling, and application settings
// ---------------------------------------------------------------------------

@description('vCPU cores allocated to the bot container. Must pair with botMemoryGi in one of Container Apps\' supported cpu:memory ratios (roughly 1 core : 2Gi) — e.g. 0.5/1, 1/2, 1.5/3, 2/4.')
param botCpuCores string = '1.0'

@description('Memory (Gi) allocated to the bot container — see botCpuCores for the required pairing.')
param botMemoryGi string = '2Gi'

@description('Minimum bot replicas. Keep at 2+ if you want spare capacity available immediately rather than scaling from zero — see azure-bot-container/README.md\'s note on Container Apps\' scale-out being a target, not a guarantee.')
@minValue(0)
@maxValue(300)
param botMinReplicas int = 1

@description('Maximum bot replicas.')
@minValue(1)
@maxValue(300)
param botMaxReplicas int = 10

@description('HTTP scale rule threshold: concurrent requests per replica before Container Apps starts scaling out. Not a hard cap (KEDA polls every ~30s) — see azure-bot-container/README.md.')
@minValue(1)
@maxValue(1000)
param botScaleRuleConcurrency int = 40

@description('Client-side read timeout (seconds) for a single GTI Agentic API call. Must stay under premiumIngressIdleTimeoutMinutes (converted to seconds) — a read timeout is not retried by app/gti/client.py, so the practical worst case stays close to this value rather than compounding across retries.')
@minValue(30)
@maxValue(1800)
param gtiTimeoutSeconds int = 600

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Seeds a JSON config blob (bot-config/output-format.json) in the storage account on first read — after that the blob is the source of truth and this value is ignored.')
param outputFormatInstructions string = ''

@description('Enable channel-thread context (Microsoft Graph) for the bot\'s queries. Requires the bot identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent — a manual one-time step. No effect outside channels.')
param threadContextEnabled bool = true

@description('Number of most-recent channel-thread messages to fetch as context per query. Only used when threadContextEnabled is true.')
@minValue(1)
@maxValue(50)
param threadContextMessageCount int = 5

@description('Non-secret environment variables merged onto the bot container (e.g. GTI_API_BASE_URL).')
param botEnv object = {}

// ---------------------------------------------------------------------------
// Teams manifest — build and upload
// ---------------------------------------------------------------------------
// Same reasoning as ../../azure/infra/main.bicep: every deployment creates a
// new bot App ID (the Managed Identity's client ID), so the manifest is
// assembled at deploy time with id/botId rewritten to match, rather than
// shipped as a static zip. Reuses the same manifest source as the Functions
// variant — the manifest itself doesn't depend on which compute hosts the bot.

@description('Blob container that receives the Teams app manifest package.')
param manifestContainerName string = 'teams-manifest'

@description('Blob name for the uploaded Teams app manifest zip.')
param manifestBlobName string = 'teams-app-manifest.zip'

@description('Base URL the Teams app manifest source files (manifest.json, color.png, outline.png) are fetched from at deploy time. Defaults to this app\'s own manifest folder (identical content to the Functions variant\'s, kept local so this deployment doesn\'t depend on the azure/ tree). Leave empty to skip the manifest upload entirely.')
param manifestSourceBaseUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure-container/azure-bot-container/teams-app-manifest'

// ---------------------------------------------------------------------------
// RS Alerts — optional background Function App (GTI Alerts -> Teams)
// ---------------------------------------------------------------------------
// Unchanged from ../../azure/infra/main.bicep except: Premium is not offered
// as a hosting plan here (Consumption or Flex Consumption only, per this
// deployment's own scope), the code zip points at this folder's copy of
// rs-alerts, and BACKFILL_DAYS is exposed (a setting added after the
// Functions template was last written).

@description('Set to true to provision RS Alerts: a background, timer-triggered Function App that posts new Google Threat Intelligence alerts to a Teams channel.')
param enableRsAlerts bool = true

@description('Name of the RS Alerts Function App. Only used when enableRsAlerts is true.')
param rsAlertsFunctionAppName string = '${containerAppName}-rs-alerts'

@description('Name of the RS Alerts App Service Plan. Only used when enableRsAlerts is true.')
param rsAlertsAppServicePlanName string = '${rsAlertsFunctionAppName}-plan'

@description('Hosting plan for the RS Alerts Function App — Consumption (classic, scale-to-zero, pay-per-execution) or FlexConsumption (modern serverless with configurable memory). Premium is deliberately not offered by this template.')
@allowed([
  'Consumption'
  'FlexConsumption'
])
param rsAlertsHostingPlanType string = 'Consumption'

@description('Per-instance memory (MB) for the RS Alerts Flex Consumption plan. Only used when rsAlertsHostingPlanType is FlexConsumption.')
@allowed([
  512
  2048
  4096
])
param rsAlertsInstanceMemoryMB int = 512

@description('Maximum scale-out instance count for the RS Alerts Flex Consumption plan. Only used when rsAlertsHostingPlanType is FlexConsumption.')
@minValue(40)
@maxValue(1000)
param rsAlertsMaximumInstanceCount int = 40

@description('Python worker runtime version for the RS Alerts Function App.')
@allowed([
  '3.9'
  '3.10'
  '3.11'
  '3.12'
])
param pythonVersion string = '3.12'

@description('Teams channel link or ID (19:xxx@thread.tacv2) that RS Alerts posts GTI alerts into. Required when enableRsAlerts is true.')
param rsAlertsTeamsChannelId string = ''

@description('RS Alert Project ID: the GTI project RS Alerts polls for alerts, from the Alerts URL (...&project=projects/<id>). Required when enableRsAlerts is true.')
param rsAlertsGtiProject string = ''

@description('NCRONTAB schedule RS Alerts polls GTI on. Default: every 15 minutes.')
param rsAlertsSchedule string = '0 */15 * * * *'

@description('Timezone for the RS Alerts schedule.')
param rsAlertsScheduleTimezone string = 'Etc/UTC'

@description('Page size for the GTI Alerts API (max 1000).')
param rsAlertsPageSize string = '1000'

@description('Backfill window (days) used to seed the cursor on RS Alerts\' very first run (no persisted state yet) — bounds how much alert history a fresh deployment pulls in, instead of the project\'s entire history. Clamped to 1-7 at runtime by the app itself if set outside that range.')
@minValue(1)
@maxValue(7)
param rsAlertsBackfillDays int = 7

@description('Filter: Severity level (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value — there is no "disable this dimension" option.')
param rsAlertsFilterSeverityLevel string = 'MEDIUM,HIGH'

@description('Filter: Priority level (comma-separated LOW/MEDIUM/HIGH/CRITICAL). Must resolve to at least one value.')
param rsAlertsFilterPriorityLevel string = 'MEDIUM,HIGH,CRITICAL'

@description('Filter: Relevance level (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterRelevanceLevel string = 'MEDIUM,HIGH'

@description('Filter: Relevance confidence (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterRelevanceConfidence string = 'MEDIUM,HIGH'

@description('Additional non-secret application settings merged onto the RS Alerts Function App.')
param rsAlertsAppSettings object = {}

@description('URL to a pre-built RS Alerts code zip (host.json etc. at the zip root). Only used when enableRsAlerts is true. Leave empty to skip automatic code deployment for RS Alerts.')
param rsAlertsCodeZipUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure-container/rs-alerts/code.zip'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

var dedicatedProfileName = 'premium-ingress'

var botCustomEnvArray = [for key in items(botEnv): {
  name: key.key
  value: key.value
}]

// Env vars for the bot container. GTI_API_KEY is deliberately absent here —
// it's wired via configuration.secrets + env[].secretRef in the container
// app resource below, not as a plain value.
var botEnvBase = [
  {
    name: 'CLIENT_ID'
    value: botIdentity.properties.clientId
  }
  {
    name: 'MANAGED_IDENTITY_CLIENT_ID'
    value: botIdentity.properties.clientId
  }
  {
    name: 'GTI_TIMEOUT_SECONDS'
    value: string(gtiTimeoutSeconds)
  }
  {
    name: 'OUTPUT_FORMAT_INSTRUCTIONS'
    value: outputFormatInstructions
  }
  {
    name: 'STORAGE_CONNECTION_STRING'
    value: storageConnectionString
  }
  {
    name: 'THREAD_CONTEXT_ENABLED'
    value: string(threadContextEnabled)
  }
  {
    name: 'THREAD_CONTEXT_MESSAGE_COUNT'
    value: string(threadContextMessageCount)
  }
]

// RS Alerts variables — mirrors ../../azure/infra/main.bicep, minus the
// Premium branch (Consumption | FlexConsumption only here).
var rsAlertsDeploymentContainerName = 'app-package-${toLower(rsAlertsFunctionAppName)}'
var rsAlertsStateContainerName = 'rs-alerts-state'
var rsAlertsCustomAppSettingsArray = [for key in items(rsAlertsAppSettings): {
  name: key.key
  value: key.value
}]

var rsAlertsPlanSkuName = rsAlertsHostingPlanType == 'FlexConsumption' ? 'FC1' : 'Y1'
var rsAlertsPlanSkuTier = rsAlertsHostingPlanType == 'FlexConsumption' ? 'FlexConsumption' : 'Dynamic'

var rsAlertsClassicPlanAppSettings = [
  {
    name: 'FUNCTIONS_EXTENSION_VERSION'
    value: '~4'
  }
  {
    name: 'FUNCTIONS_WORKER_RUNTIME'
    value: 'python'
  }
  {
    name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
    value: 'true'
  }
  {
    name: 'ENABLE_ORYX_BUILD'
    value: 'true'
  }
]

var rsAlertsAppSettingsBase = [
  {
    name: 'AzureWebJobsStorage'
    value: storageConnectionString
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsights!.properties.ConnectionString
  }
  {
    // Same identity as the bot — RS Alerts authenticates to the Bot
    // Framework Connector API (and Microsoft Graph, for Teams app
    // auto-install) as the same bot (app/bot_auth.py, app/graph_client.py).
    // No client-secret path exists — Managed Identity only.
    name: 'CLIENT_ID'
    value: botIdentity.properties.clientId
  }
  {
    name: 'MANAGED_IDENTITY_CLIENT_ID'
    value: botIdentity.properties.clientId
  }
  {
    name: 'GTI_API_KEY'
    value: '@Microsoft.KeyVault(SecretUri=${kvSecretGtiApiKey.properties.secretUri})'
  }
  {
    name: 'GTI_RSA_PROJECT'
    value: rsAlertsGtiProject
  }
  {
    name: 'TEAMS_CHANNEL_ID'
    value: rsAlertsTeamsChannelId
  }
  {
    name: 'RS_ALERTS_SCHEDULE'
    value: rsAlertsSchedule
  }
  {
    name: 'PAGE_SIZE'
    value: rsAlertsPageSize
  }
  {
    name: 'BACKFILL_DAYS'
    value: string(rsAlertsBackfillDays)
  }
  {
    name: 'WEBSITE_TIME_ZONE'
    value: rsAlertsScheduleTimezone
  }
  {
    name: 'FILTER_SEVERITY_LEVEL'
    value: rsAlertsFilterSeverityLevel
  }
  {
    name: 'FILTER_PRIORITY_LEVEL'
    value: rsAlertsFilterPriorityLevel
  }
  {
    name: 'FILTER_RELEVANCE_LEVEL'
    value: rsAlertsFilterRelevanceLevel
  }
  {
    name: 'FILTER_RELEVANCE_CONFIDENCE'
    value: rsAlertsFilterRelevanceConfidence
  }
  {
    name: 'STATE_CONTAINER_NAME'
    value: rsAlertsStateContainerName
  }
]

// Shared by the RS Alerts code-deploy deployment script — downloads the
// given pre-built code.zip (host.json etc. already at its root) and
// zip-deploys it with a remote build, so requirements.txt dependencies get
// installed server-side. Identical mechanism to ../../azure/infra/main.bicep.
var rsAlertsCodeDeployScriptContent = '''
  set -e
  python3 - "$CODE_ZIP_URL" /tmp/code.zip <<'PY'
import sys
import urllib.request

url, out_path = sys.argv[1:3]
urllib.request.urlretrieve(url, out_path)
PY

  az functionapp deployment source config-zip \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --src /tmp/code.zip \
    --build-remote true

  echo "{\"deployed\": true}" > $AZ_SCRIPTS_OUTPUT_PATH
'''

// ---------------------------------------------------------------------------
// Storage account (bot output-format/session storage + RS Alerts state)
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

// Destination for the Teams manifest zip built by manifestUpload below.
resource manifestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: manifestContainerName
  properties: {
    publicAccess: 'None'
  }
}

// app/output_format_store.py's own container (bot-config) and
// app/gti/session_store.py's table (GtiSessions) are created idempotently by
// the app itself on first use — no Bicep resource needed for either.

// RS Alerts' own deployment package storage and cursor state container —
// only created when enableRsAlerts is true.
resource rsAlertsDeploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (enableRsAlerts && rsAlertsHostingPlanType == 'FlexConsumption') {
  parent: blobServices
  name: rsAlertsDeploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource rsAlertsStateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (enableRsAlerts) {
  parent: blobServices
  name: rsAlertsStateContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

resource botIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${containerAppName}-identity'
  location: location
  tags: tags
}

// A second, dedicated identity solely for RS Alerts' zip-deploy deployment
// script below — kept separate from botIdentity (the bot's own runtime
// credential, trusted by Teams/Graph/GTI) so the elevated deploy-time role
// (Website Contributor) never lands on the identity the bot authenticates as.
// The bot itself no longer needs anything like this: its image is pulled
// straight from a public Docker Hub repository (see botDockerHubRepository),
// so there's no build/push step for this template to run at all.
var codeAutoDeployEnabled = enableRsAlerts && !empty(rsAlertsCodeZipUrl)

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (codeAutoDeployEnabled) {
  name: '${containerAppName}-deploy-identity'
  location: location
  tags: tags
}

resource deployIdentityWebsiteContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableRsAlerts && !empty(rsAlertsCodeZipUrl)) {
  name: guid(resourceGroup().id, deployIdentity!.id, 'WebsiteContributor')
  scope: resourceGroup()
  properties: {
    // Built-in "Website Contributor" role, scoped to this resource group —
    // lets this identity zip-deploy code (AAD-authenticated, no publish
    // profile/basic-auth credentials needed) to RS Alerts' Function App.
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'de139f84-1756-47ae-9be6-808fbbe84772')
    principalId: deployIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Key Vault
// ---------------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource kvSecretGtiApiKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GtiApiKey'
  properties: {
    value: gtiApiKey
  }
}

resource kvSecretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, botIdentity.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    // Built-in "Key Vault Secrets User" role — read-only access to secret
    // values. Required both for RS Alerts' @Microsoft.KeyVault(...) app
    // setting AND for the Container App's own configuration.secrets
    // Key-Vault reference below — Container Apps enforces this exact role,
    // same as Functions.
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: botIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Application Insights — RS Alerts only. The bot's Container App is observed
// through the Container Apps Environment's own Log Analytics workspace
// (below) instead — Container Apps doesn't use Application Insights the way
// Functions does, and this bot doesn't otherwise instrument itself for App
// Insights specifically (see app/logging_config.py — plain stdout logging).
// ---------------------------------------------------------------------------

resource appInsights 'Microsoft.Insights/components@2020-02-02' = if (enableRsAlerts) {
  name: '${rsAlertsFunctionAppName}-insights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Flow_Type: 'Bluefield'
    Request_Source: 'rest'
  }
}

// ---------------------------------------------------------------------------
// Container Apps Environment (Premium ingress via a Dedicated workload profile)
// ---------------------------------------------------------------------------

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: containerAppEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
      {
        name: dedicatedProfileName
        workloadProfileType: dedicatedWorkloadProfileType
        minimumCount: dedicatedWorkloadProfileMinNodeCount
        maximumCount: dedicatedWorkloadProfileMaxNodeCount
      }
    ]
    ingressConfiguration: {
      workloadProfileName: dedicatedProfileName
      requestIdleTimeout: premiumIngressIdleTimeoutMinutes
    }
  }
}

// ---------------------------------------------------------------------------
// Bot — Container App
// ---------------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2025-07-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${botIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    // Runs the bot's own replicas on the same Dedicated profile as ingress —
    // keeps the whole request path (ingress + app) off the Consumption
    // profile, matching what "Premium ingress" is generally understood to
    // mean for a deployment, not just the ingress hop in isolation.
    workloadProfileName: dedicatedProfileName
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      // No registries block: botDockerHubRepository is a public repo, so
      // Container Apps can pull it anonymously — no registry credentials
      // needed. Add one back here (server: 'docker.io', identity/username +
      // passwordSecretRef) if it's ever made private.
      secrets: [
        {
          name: 'gti-api-key'
          keyVaultUrl: kvSecretGtiApiKey.properties.secretUri
          identity: botIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'gti-teams-bot-agentic'
          image: '${botDockerHubRepository}:${botImageTag}'
          resources: {
            cpu: json(botCpuCores)
            memory: botMemoryGi
          }
          env: concat(botEnvBase, [
            {
              name: 'GTI_API_KEY'
              secretRef: 'gti-api-key'
            }
          ], botCustomEnvArray)
        }
      ]
      scale: {
        minReplicas: botMinReplicas
        maxReplicas: botMaxReplicas
        rules: [
          {
            name: 'http-scale-rule'
            http: {
              metadata: {
                concurrentRequests: string(botScaleRuleConcurrency)
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    kvSecretsUserRoleAssignment
  ]
}

// ---------------------------------------------------------------------------
// Teams manifest — build and upload
// ---------------------------------------------------------------------------

resource manifestUpload 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(manifestSourceBaseUrl)) {
  name: '${containerAppName}-manifest-upload'
  location: location
  tags: tags
  kind: 'AzureCLI'
  properties: {
    azCliVersion: '2.60.0'
    forceUpdateTag: forceUpdateTag
    retentionInterval: 'PT1H'
    timeout: 'PT10M'
    cleanupPreference: 'OnSuccess'
    environmentVariables: [
      {
        name: 'STORAGE_ACCOUNT_NAME'
        value: storageAccount.name
      }
      {
        name: 'STORAGE_ACCOUNT_KEY'
        secureValue: storageAccount.listKeys().keys[0].value
      }
      {
        name: 'CONTAINER_NAME'
        value: manifestContainerName
      }
      {
        name: 'BLOB_NAME'
        value: manifestBlobName
      }
      {
        name: 'MANIFEST_JSON_URL'
        value: '${manifestSourceBaseUrl}/manifest.json'
      }
      {
        name: 'COLOR_ICON_URL'
        value: '${manifestSourceBaseUrl}/color.png'
      }
      {
        name: 'OUTLINE_ICON_URL'
        value: '${manifestSourceBaseUrl}/outline.png'
      }
      {
        name: 'BOT_APP_ID'
        value: botIdentity.properties.clientId
      }
    ]
    // No guaranteed HTTP client in this image (curl isn't present) — fetching
    // + zipping runs through python3's stdlib instead, which ships with the
    // Azure CLI image since az itself is a Python app.
    scriptContent: '''
      set -e
      python3 - "$MANIFEST_JSON_URL" "$COLOR_ICON_URL" "$OUTLINE_ICON_URL" "$BOT_APP_ID" /tmp/manifest.zip <<'PY'
import json
import sys
import urllib.request
import zipfile

manifest_url, color_url, outline_url, bot_app_id, out_zip = sys.argv[1:6]

with urllib.request.urlopen(manifest_url) as r:
    manifest = json.load(r)

manifest["id"] = bot_app_id
for bot in manifest.get("bots", []):
    bot["botId"] = bot_app_id

with urllib.request.urlopen(color_url) as r:
    color_bytes = r.read()
with urllib.request.urlopen(outline_url) as r:
    outline_bytes = r.read()

with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    zf.writestr("color.png", color_bytes)
    zf.writestr("outline.png", outline_bytes)
PY

      az storage blob upload \
        --account-name "$STORAGE_ACCOUNT_NAME" \
        --account-key "$STORAGE_ACCOUNT_KEY" \
        --container-name "$CONTAINER_NAME" \
        --name "$BLOB_NAME" \
        --file /tmp/manifest.zip \
        --overwrite true
      echo "{\"blobUploaded\": true}" > $AZ_SCRIPTS_OUTPUT_PATH
    '''
  }
  dependsOn: [
    manifestContainer
  ]
}

// ---------------------------------------------------------------------------
// Azure Bot
// ---------------------------------------------------------------------------

@description('Name of the Azure Bot resource. Defaults to containerAppName with a unique suffix, as Azure Bot handles must be globally unique across Azure.')
param botName string = '${containerAppName}-${take(uniqueString(resourceGroup().id, subscription().id, containerAppName), 6)}'

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  location: 'global'
  tags: tags
  sku: {
    name: 'F0'
  }
  kind: 'azurebot'
  properties: {
    displayName: botName
    endpoint: 'https://${containerApp.properties.configuration.ingress.fqdn}/api/messages'
    msaAppId: botIdentity.properties.clientId
    msaAppType: 'UserAssignedMSI'
    msaAppTenantId: tenantId
    msaAppMSIResourceId: botIdentity.id
  }
}

resource botTeamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
  }
}

// ---------------------------------------------------------------------------
// RS Alerts — App Service Plan + Function App (only when enableRsAlerts)
// ---------------------------------------------------------------------------

resource rsAlertsAppServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = if (enableRsAlerts) {
  name: rsAlertsAppServicePlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: rsAlertsPlanSkuName
    tier: rsAlertsPlanSkuTier
  }
  properties: {
    reserved: true
  }
}

resource rsAlertsFunctionApp 'Microsoft.Web/sites@2023-12-01' = if (enableRsAlerts && rsAlertsHostingPlanType == 'FlexConsumption') {
  name: rsAlertsFunctionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${botIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: rsAlertsAppServicePlan.id
    httpsOnly: true
    // This site only has a User-Assigned identity, so Key Vault references
    // must name it explicitly or @Microsoft.KeyVault(...) silently fails to
    // resolve.
    keyVaultReferenceIdentity: botIdentity.id
    siteConfig: {
      appSettings: concat(rsAlertsAppSettingsBase, [
        {
          name: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          value: storageConnectionString
        }
      ], rsAlertsCustomAppSettingsArray)
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${rsAlertsDeploymentContainerName}'
          authentication: {
            type: 'StorageAccountConnectionString'
            storageAccountConnectionStringName: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: rsAlertsMaximumInstanceCount
        instanceMemoryMB: rsAlertsInstanceMemoryMB
        // Timer triggers on Flex Consumption need at least one always-ready
        // instance to fire while scaled to zero — without this, the app has
        // nothing listening for the schedule tick and the timer silently
        // never runs.
        alwaysReady: [
          {
            name: 'function:rs_alerts_timer'
            instanceCount: 1
          }
        ]
      }
      runtime: {
        name: 'python'
        version: pythonVersion
      }
    }
  }
  dependsOn: [
    rsAlertsDeploymentContainer
  ]
}

resource rsAlertsFunctionAppClassic 'Microsoft.Web/sites@2023-12-01' = if (enableRsAlerts && rsAlertsHostingPlanType == 'Consumption') {
  name: rsAlertsFunctionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${botIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: rsAlertsAppServicePlan.id
    httpsOnly: true
    keyVaultReferenceIdentity: botIdentity.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|${pythonVersion}'
      appSettings: concat(rsAlertsAppSettingsBase, rsAlertsClassicPlanAppSettings, rsAlertsCustomAppSettingsArray)
    }
  }
}

var rsAlertsFunctionAppHostName = !enableRsAlerts ? '' : (rsAlertsHostingPlanType == 'FlexConsumption' ? rsAlertsFunctionApp!.properties.defaultHostName : rsAlertsFunctionAppClassic!.properties.defaultHostName)

// ---------------------------------------------------------------------------
// Automatic code deployment — RS Alerts (only when enableRsAlerts)
// ---------------------------------------------------------------------------

resource rsAlertsCodeDeploy 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (enableRsAlerts && !empty(rsAlertsCodeZipUrl)) {
  name: '${rsAlertsFunctionAppName}-code-deploy'
  location: location
  tags: tags
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${deployIdentity.id}': {}
    }
  }
  properties: {
    azCliVersion: '2.60.0'
    forceUpdateTag: forceUpdateTag
    retentionInterval: 'PT1H'
    timeout: 'PT15M'
    cleanupPreference: 'OnSuccess'
    environmentVariables: [
      {
        name: 'CODE_ZIP_URL'
        value: rsAlertsCodeZipUrl
      }
      {
        name: 'RESOURCE_GROUP'
        value: resourceGroup().name
      }
      {
        name: 'APP_NAME'
        value: rsAlertsFunctionAppName
      }
    ]
    scriptContent: rsAlertsCodeDeployScriptContent
  }
  dependsOn: [
    rsAlertsFunctionApp
    rsAlertsFunctionAppClassic
    deployIdentityWebsiteContributor
  ]
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output botMessagingEndpoint string = 'https://${containerApp.properties.configuration.ingress.fqdn}/api/messages'
output botDockerHubRepository string = botDockerHubRepository
output botImageTag string = botImageTag
output storageAccountName string = storageAccount.name
output keyVaultName string = keyVault.name
output botName string = bot.name
output botAppId string = botIdentity.properties.clientId
output manifestContainerUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}'
output manifestBlobUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}/${manifestBlobName}'

output rsAlertsEnabled bool = enableRsAlerts
output rsAlertsFunctionAppName string = enableRsAlerts ? rsAlertsFunctionAppName : ''
output rsAlertsFunctionAppDefaultHostName string = rsAlertsFunctionAppHostName
output rsAlertsHostingPlanType string = enableRsAlerts ? rsAlertsHostingPlanType : ''
output rsAlertsCodeAutoDeployed bool = enableRsAlerts && !empty(rsAlertsCodeZipUrl)
