// =============================================================================
// GTI Teams Bot (Agentic) — Azure Functions infrastructure
// =============================================================================
// Source: https://github.com/avadhsonagara/gti-team-bot
//
// Provisions everything the bot needs to run in Azure:
//   - A Flex Consumption Python Function App hosting the bot code
//   - An Azure Bot resource wired to that Function App via a User-Assigned
//     Managed Identity — no app registration or client secret required; the
//     identity's client ID *is* the bot's App ID (Azure Bot's "UserAssignedMSI"
//     app type)
//   - A Key Vault holding GTI_API_KEY, readable by that same identity
//   - Application Insights + the storage account Flex Consumption needs
//   - The Teams app manifest package, assembled and uploaded to blob storage
//     at deploy time (see the "Teams manifest" section below for why)
//   - The bot's (and, if enabled, RS Alerts') actual application code,
//     zip-deployed with a remote build from this repo's pre-built code.zip
//     files — see "Automatic code deployment" below. Set botCodeZipUrl /
//     rsAlertsCodeZipUrl to '' to skip this and publish code yourself.
//
// Two ways to deploy this template:
//   - infra/deploy-button.bicep: a thin wrapper exposing only functionAppName /
//     gtiApiKey / instanceMemoryMB / maximumInstanceCount, used by the
//     "Deploy to Azure" button in the README.
//   - `az deployment group create --template-file infra/main.bicep`: full CLI
//     control over every parameter below (custom naming, reusing an existing
//     storage account or App Service Plan, etc.).
// =============================================================================

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

@description('Name of the Function App to create.')
param functionAppName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Tags applied to all resources.')
param tags object = {}

// ---------------------------------------------------------------------------
// Compute — Flex Consumption Function App
// ---------------------------------------------------------------------------

@description('Globally-unique Storage Account name (3-24 lowercase alphanumeric characters).')
@minLength(3)
@maxLength(24)
param storageAccountName string = toLower('${take(replace(functionAppName, '-', ''), 11)}${uniqueString(resourceGroup().id, functionAppName)}')

@description('Name of the App Service Plan to use.')
param appServicePlanName string = '${functionAppName}-plan'

@description('Set to false to reuse an existing App Service Plan named appServicePlanName in this resource group, instead of creating a new one. A Function App cannot be moved between plans of different types in-place, so this must be false when redeploying onto an app that already exists on a different plan.')
param createAppServicePlan bool = true

@description('Hosting plan for the Function App. FlexConsumption (default): serverless, scale-to-zero, pay-per-execution — what this template originally shipped with. Premium (EP1/EP2/EP3, see premiumSku): pre-warmed instances (no cold starts), VNET support, at a fixed baseline cost even when idle. The classic Consumption (Y1) plan is not offered here — it lacks reliable Python-on-Linux support.')
@allowed([
  'FlexConsumption'
  'Premium'
])
param hostingPlanType string = 'FlexConsumption'

@description('Premium plan SKU. Only used when hostingPlanType is Premium.')
@allowed([
  'EP1'
  'EP2'
  'EP3'
])
param premiumSku string = 'EP1'

@description('Python worker runtime version for the Function App.')
@allowed([
  '3.9'
  '3.10'
  '3.11'
  '3.12'
])
param pythonVersion string = '3.12'

@description('Per-instance memory (MB) for the Flex Consumption plan.')
@allowed([
  512
  2048
  4096
])
param instanceMemoryMB int = 2048

@description('Maximum scale-out instance count for the Flex Consumption plan.')
@minValue(40)
@maxValue(1000)
param maximumInstanceCount int = 100

@description('Maximum concurrent HTTP requests processed per instance (Portal: "Scale and concurrency"). Flex Consumption: 0 (default) leaves this on the system-assigned value Azure derives from instanceMemoryMB (512 MB → 4, 2048 MB → 16, 4096 MB → 32 — though the Python worker always uses 1 regardless of size); set 1-1000 to assign a fixed value manually instead, matching the portal\'s "Assign manually" option. Premium: has no platform-provided default — 0 leaves host.json\'s own extensions.http.maxConcurrentRequests in effect unmodified; a nonzero value here overrides it at runtime via the AzureFunctionsJobHost__extensions__http__maxConcurrentRequests app setting (no code redeploy needed), so pick a value sized to your own load testing.')
@minValue(0)
@maxValue(1000)
param httpPerInstanceConcurrency int = 0

@description('Non-secret application settings merged onto the Function App (e.g. GTI_API_BASE_URL).')
param appSettings object = {}

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Seeds a JSON config blob (bot-config/output-format.json) in the Function App\'s own storage account on first read — after that the blob is the source of truth and this value is ignored. Leave empty to use the built-in formatting from app/gti/prompt.md.')
param outputFormatInstructions string = ''

@description('Number of most-recent channel-thread messages to fetch as context for each query (channel thread context via Microsoft Graph — see app/teams/thread.py). Requires the bot\'s identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent (separate from its existing Bot Framework permissions) — a manual one-time step, same as TeamsAppInstallation.ReadWriteForTeam.All for RS Alerts\' auto-install feature. No effect outside channels (Teams has no thread concept for personal/group chats).')
@minValue(1)
@maxValue(50)
param threadContextMessageCount int = 5

// ---------------------------------------------------------------------------
// Bot identity & secrets
// ---------------------------------------------------------------------------

@description('Name of the Azure Bot resource. Defaults to the same name as functionAppName.')
param botName string = functionAppName

@description('Microsoft Entra tenant ID for the Azure Bot registration. Defaults to the deployment\'s own tenant.')
param tenantId string = subscription().tenantId

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param gtiApiKey string

@description('Globally-unique Key Vault name (3-24 characters) used to store gtiApiKey.')
@minLength(3)
@maxLength(24)
param keyVaultName string = toLower('kv-${take(replace(functionAppName, '-', ''), 9)}-${take(uniqueString(resourceGroup().id, functionAppName), 9)}')

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------

@description('Name of the Application Insights resource.')
param appInsightsName string = '${functionAppName}-insights'

// ---------------------------------------------------------------------------
// Teams manifest
// ---------------------------------------------------------------------------
// The manifest package is assembled at deploy time rather than shipped as a
// static zip: every deployment creates a new bot App ID (the Managed
// Identity's client ID), so a pre-built zip would always have a stale
// id/botId baked in. Instead, the deployment script below fetches the raw
// source files from manifestSourceBaseUrl, rewrites id/botId to this
// deployment's actual bot App ID, and zips the result — so the sideloadable
// package always matches the bot this deployment just created.

@description('Blob container that receives the Teams app manifest package.')
param manifestContainerName string = 'teams-manifest'

@description('Blob name for the uploaded Teams app manifest zip.')
param manifestBlobName string = 'teams-app-manifest.zip'

@description('Base URL the Teams app manifest source files (manifest.json, color.png, outline.png) are fetched from at deploy time. Defaults to this repo\'s teams-app-manifest folder. Skip the manifest upload entirely by leaving this empty.')
param manifestSourceBaseUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure/azure-bot-function/teams-app-manifest'

@description('Forces the manifest-upload deployment script to re-run on every deployment. Microsoft.Resources/deploymentScripts otherwise skips re-execution — and keeps its old environment variables (e.g. a stale storage account name) — when redeployed without this changing.')
param forceUpdateTag string = utcNow()

// ---------------------------------------------------------------------------
// RS Alerts — optional background Function App (GTI Alerts -> Teams)
// ---------------------------------------------------------------------------
// A second, independently-deployed Function App (source: ../rs-alerts) that
// polls the GTI List Alerts API on a timer and posts new alerts as Adaptive
// Cards to a Teams channel via the Bot Framework Connector API. It's fully
// optional and off by default — set enableRsAlerts to true (the "Yes" toggle
// on the Deploy to Azure form) to provision it alongside the main bot. It
// reuses the same bot identity (so it can call the Bot Framework Connector
// API and read Key Vault via the same "Key Vault Secrets User" role
// assignment), the same GTI_API_KEY secret, storage account, and
// Application Insights instance — only its own deployment container, state
// container, App Service Plan, and Function App are created separately.

@description('Set to true to provision RS Alerts: a background, timer-triggered Function App that posts new Google Threat Intelligence alerts to a Teams channel.')
param enableRsAlerts bool = false

@description('Name of the RS Alerts Function App. Only used when enableRsAlerts is true.')
param rsAlertsFunctionAppName string = '${functionAppName}-rs-alerts'

@description('Name of the RS Alerts App Service Plan. Only used when enableRsAlerts is true.')
param rsAlertsAppServicePlanName string = '${rsAlertsFunctionAppName}-plan'

@description('Hosting plan for the RS Alerts Function App. Only used when enableRsAlerts is true — see hostingPlanType above for what each option means.')
@allowed([
  'FlexConsumption'
  'Premium'
])
param rsAlertsHostingPlanType string = 'FlexConsumption'

@description('Premium plan SKU for RS Alerts. Only used when rsAlertsHostingPlanType is Premium.')
@allowed([
  'EP1'
  'EP2'
  'EP3'
])
param rsAlertsPremiumSku string = 'EP1'

@description('Teams channel link or ID (19:xxx@thread.tacv2) that RS Alerts posts GTI alerts into. Required when enableRsAlerts is true.')
param rsAlertsTeamsChannelId string = ''

@description('GTI project ID for RS Alerts, from the Alerts URL (...&project=projects/<id>). Required when enableRsAlerts is true.')
param rsAlertsGtiProject string = ''

@description('NCRONTAB schedule RS Alerts polls GTI on. Default: every 3 minutes.')
param rsAlertsSchedule string = '0 */3 * * * *'

@description('Timezone for the RS Alerts schedule.')
param rsAlertsScheduleTimezone string = 'Etc/UTC'

@description('Page size for the GTI Alerts API.')
param rsAlertsPageSize string = '1000'

@description('Per-instance memory (MB) for the RS Alerts Flex Consumption plan.')
@allowed([
  512
  2048
  4096
])
param rsAlertsInstanceMemoryMB int = 512

@description('Maximum scale-out instance count for the RS Alerts Flex Consumption plan.')
@minValue(40)
@maxValue(1000)
param rsAlertsMaximumInstanceCount int = 40

@description('Filter: Severity level (comma-separated LOW/MEDIUM/HIGH). Empty = no filter on this field.')
param rsAlertsFilterSeverityLevel string = 'MEDIUM,HIGH'

@description('Filter: Priority level (comma-separated LOW/MEDIUM/HIGH/CRITICAL). Empty = no filter on this field.')
param rsAlertsFilterPriorityLevel string = 'MEDIUM,HIGH,CRITICAL'

@description('Filter: Relevance level (comma-separated LOW/MEDIUM/HIGH). Empty = no filter on this field.')
param rsAlertsFilterRelevanceLevel string = 'MEDIUM,HIGH'

@description('Filter: Relevance confidence (comma-separated LOW/MEDIUM/HIGH). Empty = no filter on this field.')
param rsAlertsFilterRelevanceConfidence string = 'MEDIUM,HIGH'

@description('Additional non-secret application settings merged onto the RS Alerts Function App (e.g. FILTER_SEVERITY_LEVEL).')
param rsAlertsAppSettings object = {}

// ---------------------------------------------------------------------------
// Automatic code deployment (optional)
// ---------------------------------------------------------------------------
// Bicep only provisions the Function App *resource* — it has no application
// code in it until something publishes a package. To make the "Deploy to
// Azure" button (and a plain `az deployment group create`) produce a fully
// working bot with no manual `func azure functionapp publish` step, a
// deployment script (same mechanism as manifestUpload below) downloads a
// pre-built code.zip — committed to this repo alongside its source (see
// azure/azure-bot-function/code.zip and azure/rs-alerts/code.zip; rebuild
// and recommit either one whenever that app's code or dependencies change)
// — and zip-deploys it with a remote (Oryx) build to each Function App that
// gets created. Leave a URL empty to provision that Function App empty
// instead and publish code yourself.

@description('URL to a pre-built bot code zip (host.json etc. at the zip root). Fetched and zip-deployed with a remote build. Leave empty to skip automatic code deployment for the bot.')
param botCodeZipUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure/azure-bot-function/code.zip'

@description('URL to a pre-built RS Alerts code zip. Only used when enableRsAlerts is true. Leave empty to skip automatic code deployment for RS Alerts.')
param rsAlertsCodeZipUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure/rs-alerts/code.zip'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var deploymentContainerName = 'app-package-${toLower(functionAppName)}'
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

// Kept separate from the built-in (storage/App Insights) app settings below
// because those are derived from listKeys(), which cannot be referenced from
// inside a for-expression.
var customAppSettingsArray = [for key in items(appSettings): {
  name: key.key
  value: key.value
}]

// Shared by both code-deploy deployment scripts below (bot and RS Alerts) —
// only CODE_ZIP_URL/RESOURCE_GROUP/APP_NAME differ between the two, passed
// in as environment variables rather than baked into the script. Downloads
// the given pre-built code.zip (host.json etc. already at its root — see
// azure/azure-bot-function/code.zip and azure/rs-alerts/code.zip) and
// zip-deploys it with a remote build, so requirements.txt dependencies
// (never vendored into these zips) get installed server-side.
var codeDeployScriptContent = '''
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

var rsAlertsDeploymentContainerName = 'app-package-${toLower(rsAlertsFunctionAppName)}'
var rsAlertsStateContainerName = 'rs-alerts-state'
var rsAlertsCustomAppSettingsArray = [for key in items(rsAlertsAppSettings): {
  name: key.key
  value: key.value
}]

// App Service Plan sku/tier/kind per hostingPlanType. The plan resource
// shape itself (Microsoft.Web/serverfarms) doesn't change between plan
// types, only these values — the Function App resource is what actually
// needs a structurally different shape (functionAppConfig vs
// siteConfig.linuxFxVersion), handled by the two separate resources below.
var planSkuName = hostingPlanType == 'FlexConsumption' ? 'FC1' : premiumSku
var planSkuTier = hostingPlanType == 'FlexConsumption' ? 'FlexConsumption' : 'ElasticPremium'
var planKind = hostingPlanType == 'Premium' ? 'elastic' : 'functionapp'

var rsAlertsPlanSkuName = rsAlertsHostingPlanType == 'FlexConsumption' ? 'FC1' : rsAlertsPremiumSku
var rsAlertsPlanSkuTier = rsAlertsHostingPlanType == 'FlexConsumption' ? 'FlexConsumption' : 'ElasticPremium'
var rsAlertsPlanKind = rsAlertsHostingPlanType == 'Premium' ? 'elastic' : 'functionapp'

// App settings shared by both the Flex Consumption and Premium Function App
// resources below — each adds its own plan-specific settings on top
// (deployment storage config for Flex; FUNCTIONS_EXTENSION_VERSION/
// WORKER_RUNTIME/RUN_FROM_PACKAGE for Premium).
var mainAppSettingsBase = [
  {
    name: 'AzureWebJobsStorage'
    value: storageConnectionString
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsights.properties.ConnectionString
  }
  {
    // app/teams/auth.py validates inbound JWTs' audience against CLIENT_ID —
    // set here to the bot identity's client ID, which also doubles as this
    // Azure Bot's msaAppId below. app/teams/bot_client.py and
    // app/graph/client.py authenticate outbound calls via
    // MANAGED_IDENTITY_CLIENT_ID alone (see app/config.py) — this bot has no
    // client-secret path at all.
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
    name: 'OUTPUT_FORMAT_INSTRUCTIONS'
    value: outputFormatInstructions
  }
  {
    // See app/teams/thread.py — requires botIdentity to be granted the
    // Graph APPLICATION permission ChannelMessage.Read.All (see
    // threadContextMessageCount's @description above for the admin-consent step).
    name: 'THREAD_CONTEXT_MESSAGE_COUNT'
    value: string(threadContextMessageCount)
  }
]

// Settings only a classic (non-Flex) plan needs: Flex Consumption infers the
// runtime from functionAppConfig.runtime and deploys via a blob container
// (functionAppConfig.deployment) instead of WEBSITE_RUN_FROM_PACKAGE.
var classicPlanAppSettings = [
  {
    name: 'FUNCTIONS_EXTENSION_VERSION'
    value: '~4'
  }
  {
    name: 'FUNCTIONS_WORKER_RUNTIME'
    value: 'python'
  }
  {
    name: 'WEBSITE_RUN_FROM_PACKAGE'
    value: '1'
  }
]

// Overrides host.json's extensions.http.maxConcurrentRequests at runtime —
// only meaningful on the classic (Premium) plan, and only when the deployer
// asked for a specific value; 0 leaves host.json's own value alone. RS
// Alerts has no HTTP trigger, so it has no equivalent setting.
var httpConcurrencyAppSettings = httpPerInstanceConcurrency > 0 ? [
  {
    name: 'AzureFunctionsJobHost__extensions__http__maxConcurrentRequests'
    value: string(httpPerInstanceConcurrency)
  }
] : []

var rsAlertsAppSettingsBase = [
  {
    name: 'AzureWebJobsStorage'
    value: storageConnectionString
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsights.properties.ConnectionString
  }
  {
    // Same User-Assigned Managed Identity as the main bot — its client ID
    // is also the Azure Bot's msaAppId, so RS Alerts authenticates to the
    // Bot Framework Connector API (and Microsoft Graph, for Teams app
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

// ---------------------------------------------------------------------------
// Storage account (Function App deployment storage + Teams manifest blobs)
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

// Flex Consumption's own deployment package storage.
resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Destination for the Teams manifest zip built by manifestUpload below.
resource manifestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: manifestContainerName
  properties: {
    publicAccess: 'None'
  }
}

// RS Alerts' own deployment package storage and cursor state container —
// only created when enableRsAlerts is true.
resource rsAlertsDeploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (enableRsAlerts) {
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
// Identity — shared by the Function App, the Azure Bot, and Key Vault access
// ---------------------------------------------------------------------------
// A User-Assigned (not System-Assigned) identity is required here: Azure
// Bot's "UserAssignedMSI" app type needs a stable client ID it can be
// configured with directly, and the same identity must be attached to the
// Function App so its runtime can request tokens under that identity.

resource botIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${functionAppName}-identity'
  location: location
  tags: tags
}

// A second, dedicated identity solely for the code-deploy deployment scripts
// below — kept separate from botIdentity (the bot's own runtime credential,
// trusted by Teams/Graph/GTI) so the Website Contributor role needed to
// zip-deploy code never ends up on the identity the bot authenticates as.
var codeAutoDeployEnabled = !empty(botCodeZipUrl) || (enableRsAlerts && !empty(rsAlertsCodeZipUrl))

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (codeAutoDeployEnabled) {
  name: '${functionAppName}-deploy-identity'
  location: location
  tags: tags
}

resource deployIdentityWebsiteContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (codeAutoDeployEnabled) {
  name: guid(resourceGroup().id, functionAppName, 'WebsiteContributor', 'deployIdentity')
  scope: resourceGroup()
  properties: {
    // Built-in "Website Contributor" role, scoped to this resource group —
    // lets this identity zip-deploy code (via the SCM /api/zipdeploy
    // endpoint, Azure AD-authenticated, no publish profile/basic-auth
    // credentials needed) to the Function Apps this deployment creates,
    // without granting access to storage, Key Vault, or botIdentity itself.
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
    // Built-in "Key Vault Secrets User" role — read-only access to secret values.
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: botIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Application Insights
// ---------------------------------------------------------------------------

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
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
// App Service Plan
// ---------------------------------------------------------------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = if (createAppServicePlan) {
  name: appServicePlanName
  location: location
  tags: tags
  kind: planKind
  sku: {
    name: planSkuName
    tier: planSkuTier
  }
  properties: {
    reserved: true
  }
}

resource existingAppServicePlan 'Microsoft.Web/serverfarms@2023-12-01' existing = if (!createAppServicePlan) {
  name: appServicePlanName
}

// ---------------------------------------------------------------------------
// Function App
// ---------------------------------------------------------------------------
// Flex Consumption's deployment/scaling config (functionAppConfig) is a
// structurally different shape from Premium's (siteConfig.linuxFxVersion +
// standard app settings) — mutually exclusive, so these are two separate
// resources gated by hostingPlanType, not one resource with conditional
// properties.

resource functionApp 'Microsoft.Web/sites@2023-12-01' = if (hostingPlanType == 'FlexConsumption') {
  name: functionAppName
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
    serverFarmId: createAppServicePlan ? appServicePlan.id : existingAppServicePlan.id
    httpsOnly: true
    // Key Vault references default to the site's System-Assigned identity;
    // since this app only has a User-Assigned one, it must be named explicitly
    // or the @Microsoft.KeyVault(...) app setting below silently fails to resolve.
    keyVaultReferenceIdentity: botIdentity.id
    siteConfig: {
      appSettings: concat(mainAppSettingsBase, [
        {
          name: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          value: storageConnectionString
        }
      ], customAppSettingsArray)
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'StorageAccountConnectionString'
            storageAccountConnectionStringName: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          }
        }
      }
      scaleAndConcurrency: union({
        maximumInstanceCount: maximumInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }, httpPerInstanceConcurrency > 0 ? {
        // Matches the portal's "Assign manually" option; omitting this
        // (httpPerInstanceConcurrency == 0) leaves it on "Use a
        // system-assigned number", derived from instanceMemoryMB.
        triggers: {
          http: {
            perInstanceConcurrency: httpPerInstanceConcurrency
          }
        }
      } : {})
      runtime: {
        name: 'python'
        version: pythonVersion
      }
    }
  }
  dependsOn: [
    deploymentContainer
  ]
}

resource functionAppClassic 'Microsoft.Web/sites@2023-12-01' = if (hostingPlanType != 'FlexConsumption') {
  name: functionAppName
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
    serverFarmId: createAppServicePlan ? appServicePlan.id : existingAppServicePlan.id
    httpsOnly: true
    keyVaultReferenceIdentity: botIdentity.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|${pythonVersion}'
      // hostingPlanType is guaranteed 'Premium' here (the only non-Flex
      // option) — always-on keeps the pre-warmed instance from idling out.
      alwaysOn: true
      appSettings: concat(mainAppSettingsBase, classicPlanAppSettings, httpConcurrencyAppSettings, customAppSettingsArray)
    }
  }
}

// Whichever of the two Function App resources above actually got created,
// for every reference below that only cares about the running app, not
// which plan it's on.
var mainFunctionAppHostName = hostingPlanType == 'FlexConsumption' ? functionApp.properties.defaultHostName : functionAppClassic!.properties.defaultHostName

// ---------------------------------------------------------------------------
// Teams manifest — build and upload
// ---------------------------------------------------------------------------

resource manifestUpload 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(manifestSourceBaseUrl)) {
  name: '${functionAppName}-manifest-upload'
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
    // The container has no guaranteed HTTP client (curl isn't present in this
    // image), so fetching + zipping runs through python3's stdlib instead —
    // it ships with the Azure CLI image, since az itself is a Python app.
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
    endpoint: 'https://${mainFunctionAppHostName}/api/messages'
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
  kind: rsAlertsPlanKind
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
    // Same reasoning as the main Function App: this site only has a
    // User-Assigned identity, so Key Vault references must name it explicitly.
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

resource rsAlertsFunctionAppClassic 'Microsoft.Web/sites@2023-12-01' = if (enableRsAlerts && rsAlertsHostingPlanType != 'FlexConsumption') {
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
      // rsAlertsHostingPlanType is guaranteed 'Premium' here (the only
      // non-Flex option). Unlike Flex Consumption's alwaysReady hint above,
      // Premium's timer trigger fires correctly on its own as long as the
      // instance never scales to zero, which alwaysOn guarantees.
      alwaysOn: true
      appSettings: concat(rsAlertsAppSettingsBase, classicPlanAppSettings, rsAlertsCustomAppSettingsArray)
    }
  }
}

// Whichever of the two RS Alerts Function App resources above actually got
// created (when enabled) — for the output below, mirroring
// mainFunctionAppHostName's reasoning.
var rsAlertsFunctionAppHostName = !enableRsAlerts ? '' : (rsAlertsHostingPlanType == 'FlexConsumption' ? rsAlertsFunctionApp!.properties.defaultHostName : rsAlertsFunctionAppClassic!.properties.defaultHostName)

// ---------------------------------------------------------------------------
// Automatic code deployment — bot
// ---------------------------------------------------------------------------

resource botCodeDeploy 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(botCodeZipUrl)) {
  name: '${functionAppName}-code-deploy'
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
        value: botCodeZipUrl
      }
      {
        name: 'RESOURCE_GROUP'
        value: resourceGroup().name
      }
      {
        name: 'APP_NAME'
        value: functionAppName
      }
    ]
    scriptContent: codeDeployScriptContent
  }
  dependsOn: [
    functionApp
    functionAppClassic
    deployIdentityWebsiteContributor
  ]
}

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
    scriptContent: codeDeployScriptContent
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

output functionAppName string = functionAppName
output functionAppDefaultHostName string = mainFunctionAppHostName
output functionAppMessagingEndpoint string = 'https://${mainFunctionAppHostName}/api/messages'
output hostingPlanType string = hostingPlanType
output storageAccountName string = storageAccount.name
output appInsightsName string = appInsights.name
output keyVaultName string = keyVault.name
output botName string = bot.name
output botAppId string = botIdentity.properties.clientId
output manifestContainerUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}'
output manifestBlobUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}/${manifestBlobName}'
output botCodeAutoDeployed bool = !empty(botCodeZipUrl)
output rsAlertsCodeAutoDeployed bool = enableRsAlerts && !empty(rsAlertsCodeZipUrl)

output rsAlertsEnabled bool = enableRsAlerts
output rsAlertsFunctionAppName string = enableRsAlerts ? rsAlertsFunctionAppName : ''
output rsAlertsFunctionAppDefaultHostName string = rsAlertsFunctionAppHostName
output rsAlertsHostingPlanType string = enableRsAlerts ? rsAlertsHostingPlanType : ''
