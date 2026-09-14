// =============================================================================
// GTI Teams Bot (Agentic) — Asynchronous Queue Architecture infrastructure
// =============================================================================
// Provisions the 2-tier queue architecture in ../gti-teams-bot:
//   - bot-ingest-function: an HTTP-triggered Function App on a Consumption
//     plan ONLY. Verifies the inbound Bot Framework request, posts the
//     "looking into that…" placeholder, and enqueues a job — always
//     responds in well under a second, regardless of how long the GTI query
//     itself ends up taking.
//   - bot-worker-function: a queue-triggered Function App on Consumption OR
//     Flex Consumption (default). Does the actual GTI Agentic query — which
//     can take minutes — and delivers the final response by editing/
//     replacing the placeholder bot-ingest-function already posted.
//   - One shared User-Assigned Managed Identity (both apps + the Azure Bot
//     registration authenticate as this one identity — no client secret
//     anywhere), one shared Storage Account (Storage Queue "gti-query-jobs",
//     Table Storage session continuity, Blob output-format config), one
//     shared Key Vault (GTI_API_KEY), one shared Application Insights.
//   - An Azure Bot resource (UserAssignedMSI) wired to bot-ingest-function's
//     messaging endpoint, with the Teams channel enabled.
//   - The Teams app manifest package (assembled at deploy time — see "Teams
//     manifest" below) and each Function App's code (zip-deployed with a
//     remote build — see "Automatic code deployment" below), both fetched
//     by default from this repo's own GitHub-hosted copies. Set the
//     corresponding *Url parameter to empty to skip either step and handle
//     it yourself instead.
//
// Two ways to deploy this template:
//   - deploy-button.bicep: a thin wrapper exposing only the handful of
//     parameters a first-time deployer actually needs, used by the
//     "Deploy to Azure" button.
//   - `az deployment group create --template-file main.bicep`: full CLI
//     control over every parameter below.
// =============================================================================

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

@description('Name of the Ingest Function App (HTTP-triggered messaging endpoint).')
param ingestFunctionAppName string = 'gti-teams-bot-ingest'

@description('Name of the Worker Function App (queue-triggered GTI processing).')
param workerFunctionAppName string = 'gti-teams-bot-worker'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Tags applied to all resources.')
param tags object = {}

@description('Python worker runtime version for both Function Apps.')
@allowed([
  '3.9'
  '3.10'
  '3.11'
  '3.12'
])
param pythonVersion string = '3.12'

// ---------------------------------------------------------------------------
// Shared storage (deployment storage for both apps, the job queue, Table
// Storage session continuity, and Blob output-format config)
// ---------------------------------------------------------------------------

@description('Globally-unique Storage Account name (3-24 lowercase alphanumeric characters), shared by both Function Apps.')
@minLength(3)
@maxLength(24)
param storageAccountName string = toLower('${take(replace(ingestFunctionAppName, '-', ''), 11)}${uniqueString(resourceGroup().id, ingestFunctionAppName)}')

@description('Name of the Storage Queue the Ingest Function enqueues jobs onto and the Worker Function is triggered from. Must match both apps\' JOB_QUEUE_NAME app setting exactly (set automatically below).')
param jobQueueName string = 'gti-query-jobs'

// ---------------------------------------------------------------------------
// Ingest Function App — Consumption plan only
// ---------------------------------------------------------------------------
// No hostingPlanType parameter here at all: bot-ingest-function's whole
// design point is to always respond in well under a second (it never calls
// GTI itself), so it has no need for Flex Consumption's configurable
// memory/concurrency or Premium's pre-warmed instances — plain Consumption
// (scale-to-zero, pay-per-execution) is the only supported option.

@description('Name of the Ingest Function App\'s App Service Plan.')
param ingestAppServicePlanName string = '${ingestFunctionAppName}-plan'

@description('Set to false to reuse an existing Consumption plan named ingestAppServicePlanName in this resource group, instead of creating a new one.')
param createIngestAppServicePlan bool = true

@description('Maximum concurrent HTTP requests processed per Ingest instance (Portal: "Scale and concurrency" -> "Assign manually"), applied via the AzureFunctionsJobHost__extensions__http__maxConcurrentRequests app setting. Also sets PYTHON_THREADPOOL_THREAD_COUNT to the same value, since each concurrent request is handled on its own thread within one worker process for this fully-synchronous (non-async) app.')
@minValue(1)
@maxValue(32)
param ingestConcurrentRequests int = 20

@description('Minimum instance count for the Ingest Function App. Classic Consumption plan scales to zero when idle (0).')
@minValue(0)
@maxValue(100)
param ingestMinimumInstanceCount int = 0

@description('Maximum scale-out instance count for the Ingest Function App (functionAppScaleLimit on Consumption plan).')
@minValue(1)
@maxValue(1000)
param ingestMaximumInstanceCount int = 5

// ---------------------------------------------------------------------------
// Worker Function App — Consumption or Flex Consumption
// ---------------------------------------------------------------------------

@description('Hosting plan for the Worker Function App. Consumption: classic scale-to-zero, pay-per-execution. Flex Consumption (default): modern serverless with configurable per-instance memory. Premium is deliberately not offered here — the worker\'s whole cost model depends on paying only while a job is actually running.')
@allowed([
  'Consumption'
  'FlexConsumption'
])
param workerHostingPlanType string = 'FlexConsumption'

@description('Name of the Worker Function App\'s App Service Plan.')
param workerAppServicePlanName string = '${workerFunctionAppName}-plan'

@description('Set to false to reuse an existing plan named workerAppServicePlanName in this resource group, instead of creating a new one. A Function App cannot move between plans of different types in-place, so this must be false when redeploying onto a worker that already exists on a different plan type.')
param createWorkerAppServicePlan bool = true

@description('Per-instance memory (MB) for the Worker Function App when workerHostingPlanType is FlexConsumption. Ignored for Consumption.')
@allowed([
  512
  2048
  4096
])
param workerInstanceMemoryMB int = 2048

@description('Number of queue messages the Worker Function processes concurrently per instance (host.json\'s extensions.queues.batchSize, overridden here via AzureFunctionsJobHost__extensions__queues__batchSize so the deployed code\'s own host.json — which defaults to a conservative batchSize of 1 for safe standalone/manual deployment — is never edited). newBatchThreshold is set to half this value, mirroring Azure Functions\' own default 16/8 ratio. Also sets PYTHON_THREADPOOL_THREAD_COUNT to the same value.')
@minValue(1)
@maxValue(32)
param workerConcurrentRequests int = 15

@description('Minimum instance count for the Worker Function App. On Flex Consumption, setting a value > 0 keeps that number of always-ready instances pre-warmed for the queue trigger. Ignored on Consumption plan (scales to zero).')
@minValue(0)
@maxValue(100)
param workerMinimumInstanceCount int = 0

@description('Requested maximum scale-out instance count for the Worker Function App. On Consumption this becomes an exact functionAppScaleLimit cap (1 is a valid value there). On Flex Consumption, Azure enforces a hard platform floor of 40 on maximumInstanceCount regardless of what\'s requested here — a value below 40 is silently raised to 40 for that plan type only (see workerAppliedMaxInstanceCount output for the value actually applied).')
@minValue(1)
@maxValue(1000)
param workerMaximumInstanceCount int = 5

@description('Client-side read timeout (seconds) for a single GTI Agentic API call (GTI_TIMEOUT_SECONDS app setting). Keep this comfortably under the Worker Function App\'s own functionTimeout (baked into the deployed code\'s host.json) so the platform never force-kills an invocation before the GTI client\'s own timeout has a chance to raise a friendly error.')
@minValue(30)
@maxValue(1800)
param gtiTimeoutSeconds int = 480

@description('A dequeued job older than this (seconds, MAX_JOB_AGE_SECONDS app setting) is treated as stale and dropped with an apology instead of running an expensive GTI query for it.')
@minValue(60)
@maxValue(1800)
param maxJobAgeSeconds int = 480

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Seeds a JSON config blob (bot-config/output-format.json) in the shared storage account on first read — after that the blob is the source of truth and this value is ignored. Leave empty to use the built-in formatting from app/gti/prompt.md.')
param outputFormatInstructions string = ''

@description('Number of most-recent channel-thread messages to fetch as context for each query (channel thread context via Microsoft Graph). Requires the bot\'s identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent — a manual one-time step (see the README\'s Graph permissions section for the other two permissions channel/group-chat attachments also need). No effect outside channels.')
@minValue(1)
@maxValue(50)
param threadContextMessageCount int = 5

// ---------------------------------------------------------------------------
// Bot identity & secrets
// ---------------------------------------------------------------------------

@description('Name of the Azure Bot resource. Defaults to the Ingest Function App name with a unique suffix, as Azure Bot handles must be globally unique across Azure.')
param botName string = '${ingestFunctionAppName}-${take(uniqueString(resourceGroup().id, subscription().id, ingestFunctionAppName), 6)}'

@description('Microsoft Entra tenant ID for the Azure Bot registration. Defaults to the deployment\'s own tenant.')
param tenantId string = subscription().tenantId

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting. Only the Worker Function App reads it — the Ingest Function never calls GTI at all.')
@secure()
param gtiApiKey string

@description('Globally-unique Key Vault name (3-24 characters) used to store gtiApiKey.')
@minLength(3)
@maxLength(24)
param keyVaultName string = toLower('kv-${take(replace(ingestFunctionAppName, '-', ''), 8)}-${take(uniqueString(resourceGroup().id, subscription().id, ingestFunctionAppName), 9)}')

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------

@description('Name of the Application Insights resource, shared by both Function Apps.')
param appInsightsName string = '${ingestFunctionAppName}-insights'

// ---------------------------------------------------------------------------
// Teams manifest (optional)
// ---------------------------------------------------------------------------
// Off by default (manifestSourceBaseUrl empty): unlike azure/azure-bot-function,
// this repo publishes ../gti-teams-bot/teams-app-manifest at a stable public
// URL (raw.githubusercontent.com, confirmed reachable) for the deployment
// script to fetch. The script rewrites manifest.json's id/botId to this
// deployment's actual bot App ID before zipping, the same way
// azure/infra/main.bicep's manifestUpload does, so the sideloadable package
// always matches the bot this deployment just created.

@description('Blob container that receives the Teams app manifest package.')
param manifestContainerName string = 'teams-manifest'

@description('Blob name for the uploaded Teams app manifest zip.')
param manifestBlobName string = 'teams-app-manifest.zip'

@description('Base URL the Teams app manifest source files (manifest.json, color.png, outline.png) are fetched from at deploy time. Defaults to this repo\'s own gti-teams-bot/teams-app-manifest folder. Leave empty to skip the manifest upload entirely and assemble/sideload it yourself.')
param manifestSourceBaseUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure-queue/gti-teams-bot/teams-app-manifest'

@description('Forces the manifest-upload deployment script to re-run on every deployment. Microsoft.Resources/deploymentScripts otherwise skips re-execution — and keeps its old environment variables (e.g. a stale storage account name) — when redeployed without this changing.')
param forceUpdateTag string = utcNow()

// ---------------------------------------------------------------------------
// Automatic code deployment (optional)
// ---------------------------------------------------------------------------
// Bicep only provisions the Function App *resources* — they have no
// application code until something publishes a package. Defaults to this
// repo's own pre-built code.zip for each app (see
// ../gti-teams-bot/bot-ingest-function/code.zip and
// ../bot-worker-function/code.zip — rebuild either with `git archive
// --format=zip -o <app>/code.zip HEAD:azure-queue/gti-teams-bot/<app>` after
// changing that app's code or dependencies, then commit and push before
// redeploying). Leave a URL empty to skip automatic deployment for that app
// and publish it yourself instead, e.g. `func azure functionapp publish
// <name> --python` from within its folder.

@description('URL to a pre-built bot-ingest-function code zip (host.json etc. at the zip root). Fetched and zip-deployed with a remote build. Leave empty to skip automatic code deployment and publish it yourself.')
param ingestCodeZipUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure-queue/gti-teams-bot/bot-ingest-function/code.zip'

@description('URL to a pre-built bot-worker-function code zip. Leave empty to skip automatic code deployment and publish it yourself.')
param workerCodeZipUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure-queue/gti-teams-bot/bot-worker-function/code.zip'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

var workerDeploymentContainerName = 'app-package-${toLower(workerFunctionAppName)}'

// Real, Azure-enforced platform floor for Flex Consumption's
// scaleAndConcurrency.maximumInstanceCount — requesting less than this is
// rejected at deployment time, not silently clamped by the platform itself,
// so this template clamps it up-front instead of letting `az deployment
// group create` fail with a confusing service-side error. Only applies to
// the Flex Consumption resource below; the classic Consumption resource's
// functionAppScaleLimit has no such floor and uses workerMaximumInstanceCount
// directly.
var workerFlexMaximumInstanceCount = max(workerMaximumInstanceCount, 40)

var workerNewBatchThreshold = max(1, workerConcurrentRequests / 2)

var workerPlanSkuName = workerHostingPlanType == 'FlexConsumption' ? 'FC1' : 'Y1'
var workerPlanSkuTier = workerHostingPlanType == 'FlexConsumption' ? 'FlexConsumption' : 'Dynamic'

// Shared by both code-deploy deployment scripts below (ingest and worker) —
// only CODE_ZIP_URL/APP_NAME differ between the two, passed in as
// environment variables rather than baked into the script. Downloads the
// given pre-built code.zip and zip-deploys it with a remote build, so
// requirements.txt dependencies (never vendored into these zips) get
// installed server-side.
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

var codeAutoDeployEnabled = !empty(ingestCodeZipUrl) || !empty(workerCodeZipUrl)

// ---------------------------------------------------------------------------
// Shared app settings
// ---------------------------------------------------------------------------

var sharedCoreAppSettings = [
  {
    name: 'AzureWebJobsStorage'
    value: storageConnectionString
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsights.properties.ConnectionString
  }
  {
    // Both apps authenticate as the same bot: app/teams/auth.py (ingest)
    // validates inbound JWTs' audience against this; app/teams/bot_client.py
    // and app/graph/client.py (both apps) authenticate outbound calls via
    // MANAGED_IDENTITY_CLIENT_ID alone — no client-secret path anywhere.
    name: 'CLIENT_ID'
    value: botIdentity.properties.clientId
  }
  {
    name: 'MANAGED_IDENTITY_CLIENT_ID'
    value: botIdentity.properties.clientId
  }
  {
    name: 'JOB_QUEUE_NAME'
    value: jobQueueName
  }
]

var ingestAppSettings = concat(sharedCoreAppSettings, [
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
  {
    // Overrides host.json's extensions.http.maxConcurrentRequests at
    // runtime — no code redeploy needed to retune this.
    name: 'AzureFunctionsJobHost__extensions__http__maxConcurrentRequests'
    value: string(ingestConcurrentRequests)
  }
  {
    name: 'PYTHON_THREADPOOL_THREAD_COUNT'
    value: string(ingestConcurrentRequests)
  }
])

var workerAppSettingsBase = concat(sharedCoreAppSettings, [
  {
    name: 'GTI_API_KEY'
    value: '@Microsoft.KeyVault(SecretUri=${kvSecretGtiApiKey.properties.secretUri})'
  }
  {
    name: 'GTI_TIMEOUT_SECONDS'
    value: string(gtiTimeoutSeconds)
  }
  {
    name: 'MAX_JOB_AGE_SECONDS'
    value: string(maxJobAgeSeconds)
  }
  {
    name: 'OUTPUT_FORMAT_INSTRUCTIONS'
    value: outputFormatInstructions
  }
  {
    name: 'THREAD_CONTEXT_MESSAGE_COUNT'
    value: string(threadContextMessageCount)
  }
  {
    // Overrides host.json's extensions.queues.batchSize/newBatchThreshold at
    // runtime, without editing the deployed code's own host.json (which
    // defaults to a conservative batchSize of 1 for safe standalone/manual
    // deployment).
    name: 'AzureFunctionsJobHost__extensions__queues__batchSize'
    value: string(workerConcurrentRequests)
  }
  {
    name: 'AzureFunctionsJobHost__extensions__queues__newBatchThreshold'
    value: string(workerNewBatchThreshold)
  }
  {
    name: 'PYTHON_THREADPOOL_THREAD_COUNT'
    value: string(workerConcurrentRequests)
  }
])

// Settings only the classic (non-Flex) Consumption plan needs: Flex
// Consumption infers the runtime from functionAppConfig.runtime and deploys
// via a blob container (functionAppConfig.deployment); classic plans use
// Oryx remote build via SCM_DO_BUILD_DURING_DEPLOYMENT/ENABLE_ORYX_BUILD.
var workerClassicPlanAppSettings = [
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

// ---------------------------------------------------------------------------
// Storage account (shared: job queue, Table Storage sessions, Blob
// output-format config, and each Function App's own deployment storage)
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

resource queueServices 'Microsoft.Storage/storageAccounts/queueServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

// Pre-created for a fully-provisioned deployment (rather than relying on
// bot-ingest-function's own runtime create_queue() call on first use) — the
// poison queue "<jobQueueName>-poison" is deliberately NOT created here: the
// Azure Functions Storage extension creates it automatically, and only once
// a message actually needs poisoning.
resource jobQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueServices
  name: jobQueueName
}

// Flex Consumption's own deployment package storage — only the worker may
// need this; the ingest app is always classic Consumption (Oryx remote
// build, no blob-container deployment).
resource workerDeploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (workerHostingPlanType == 'FlexConsumption') {
  parent: blobServices
  name: workerDeploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Destination for the Teams manifest zip built by manifestUpload below.
resource manifestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (!empty(manifestSourceBaseUrl)) {
  parent: blobServices
  name: manifestContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Identity — shared by both Function Apps, the Azure Bot, and Key Vault access
// ---------------------------------------------------------------------------
// A User-Assigned (not System-Assigned) identity is required here: Azure
// Bot's "UserAssignedMSI" app type needs a stable client ID it can be
// configured with directly, and the SAME identity must be attached to BOTH
// Function Apps so either one's runtime can request tokens under it (the
// worker uses it for the Bot Framework Connector API and Microsoft Graph;
// the ingest function uses it only for the Connector API).

resource botIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${ingestFunctionAppName}-identity'
  location: location
  tags: tags
}

// A second, dedicated identity solely for the code-deploy deployment
// scripts below — kept separate from botIdentity (the bot's own runtime
// credential, trusted by Teams/Graph/GTI) so the Website Contributor role
// needed to zip-deploy code never ends up on the identity the bot
// authenticates as.
resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (codeAutoDeployEnabled) {
  name: '${ingestFunctionAppName}-deploy-identity'
  location: location
  tags: tags
}

resource deployIdentityWebsiteContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (codeAutoDeployEnabled) {
  name: guid(resourceGroup().id, deployIdentity!.id, 'WebsiteContributor')
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
    // Built-in "Key Vault Secrets User" role — read-only access to secret
    // values. Only bot-worker-function actually reads GTI_API_KEY, but the
    // role is assigned to the one identity shared by both apps.
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
// Ingest Function App — App Service Plan (Consumption only) + Function App
// ---------------------------------------------------------------------------

resource ingestAppServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = if (createIngestAppServicePlan) {
  name: ingestAppServicePlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

resource existingIngestAppServicePlan 'Microsoft.Web/serverfarms@2023-12-01' existing = if (!createIngestAppServicePlan) {
  name: ingestAppServicePlanName
}

resource ingestFunctionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: ingestFunctionAppName
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
    serverFarmId: createIngestAppServicePlan ? ingestAppServicePlan.id : existingIngestAppServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|${pythonVersion}'
      // Consumption plans scale to zero and reject alwaysOn=true.
      alwaysOn: false
      // Maximum scale-out instance count (functionAppScaleLimit) on Consumption plan.
      functionAppScaleLimit: ingestMaximumInstanceCount
      appSettings: ingestAppSettings
    }
  }
}

// ---------------------------------------------------------------------------
// Worker Function App — App Service Plan (Consumption or Flex Consumption)
// + Function App
// ---------------------------------------------------------------------------
// Flex Consumption's deployment/scaling config (functionAppConfig) is a
// structurally different shape from classic Consumption's (siteConfig.
// linuxFxVersion + standard app settings) — mutually exclusive, so these are
// two separate resources gated by workerHostingPlanType, not one resource
// with conditional properties.

resource workerAppServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = if (createWorkerAppServicePlan) {
  name: workerAppServicePlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: workerPlanSkuName
    tier: workerPlanSkuTier
  }
  properties: {
    reserved: true
  }
}

resource existingWorkerAppServicePlan 'Microsoft.Web/serverfarms@2023-12-01' existing = if (!createWorkerAppServicePlan) {
  name: workerAppServicePlanName
}

resource workerFunctionAppFlex 'Microsoft.Web/sites@2023-12-01' = if (workerHostingPlanType == 'FlexConsumption') {
  name: workerFunctionAppName
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
    serverFarmId: createWorkerAppServicePlan ? workerAppServicePlan.id : existingWorkerAppServicePlan.id
    httpsOnly: true
    // Key Vault references default to the site's System-Assigned identity;
    // since this app only has a User-Assigned one, it must be named
    // explicitly or the @Microsoft.KeyVault(...) GTI_API_KEY setting above
    // silently fails to resolve.
    keyVaultReferenceIdentity: botIdentity.id
    siteConfig: {
      appSettings: concat(workerAppSettingsBase, [
        {
          name: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          value: storageConnectionString
        }
      ])
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${workerDeploymentContainerName}'
          authentication: {
            type: 'StorageAccountConnectionString'
            storageAccountConnectionStringName: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          }
        }
      }
      scaleAndConcurrency: {
        // See workerFlexMaximumInstanceCount's own comment — clamped up to
        // Flex Consumption's real, enforced 40-instance floor.
        maximumInstanceCount: workerFlexMaximumInstanceCount
        instanceMemoryMB: workerInstanceMemoryMB
        alwaysReady: workerMinimumInstanceCount > 0 ? [
          {
            name: 'function:process_query_job'
            instanceCount: workerMinimumInstanceCount
          }
        ] : []
      }
      runtime: {
        name: 'python'
        version: pythonVersion
      }
    }
  }
  dependsOn: [
    workerDeploymentContainer
  ]
}

resource workerFunctionAppClassic 'Microsoft.Web/sites@2023-12-01' = if (workerHostingPlanType == 'Consumption') {
  name: workerFunctionAppName
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
    serverFarmId: createWorkerAppServicePlan ? workerAppServicePlan.id : existingWorkerAppServicePlan.id
    httpsOnly: true
    keyVaultReferenceIdentity: botIdentity.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|${pythonVersion}'
      alwaysOn: false
      // Unlike Flex Consumption, classic Consumption's functionAppScaleLimit
      // has no enforced minimum — workerMaximumInstanceCount is used as-is.
      functionAppScaleLimit: workerMaximumInstanceCount
      appSettings: concat(workerAppSettingsBase, workerClassicPlanAppSettings)
    }
  }
}

// Whichever of the two Worker Function App resources above actually got
// created, for every reference below that only cares about the running app,
// not which plan it's on.
var workerFunctionAppHostName = workerHostingPlanType == 'FlexConsumption' ? workerFunctionAppFlex!.properties.defaultHostName : workerFunctionAppClassic!.properties.defaultHostName

// ---------------------------------------------------------------------------
// Teams manifest — build and upload (only when manifestSourceBaseUrl is set)
// ---------------------------------------------------------------------------

resource manifestUpload 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(manifestSourceBaseUrl)) {
  name: '${ingestFunctionAppName}-manifest-upload'
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
    // The container has no guaranteed HTTP client (curl isn't present in
    // this image), so fetching + zipping runs through python3's stdlib
    // instead — it ships with the Azure CLI image, since az itself is a
    // Python app.
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
// Endpoint is bot-ingest-function's — that's the only one of the two apps
// with an HTTP messaging endpoint at all.

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
    endpoint: 'https://${ingestFunctionApp.properties.defaultHostName}/api/messages'
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
// Automatic code deployment — Ingest (only when ingestCodeZipUrl is set)
// ---------------------------------------------------------------------------

resource ingestCodeDeploy 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(ingestCodeZipUrl)) {
  name: '${ingestFunctionAppName}-code-deploy'
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
        value: ingestCodeZipUrl
      }
      {
        name: 'RESOURCE_GROUP'
        value: resourceGroup().name
      }
      {
        name: 'APP_NAME'
        value: ingestFunctionAppName
      }
    ]
    scriptContent: codeDeployScriptContent
  }
  dependsOn: [
    ingestFunctionApp
    deployIdentityWebsiteContributor
  ]
}

// ---------------------------------------------------------------------------
// Automatic code deployment — Worker (only when workerCodeZipUrl is set)
// ---------------------------------------------------------------------------

resource workerCodeDeploy 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(workerCodeZipUrl)) {
  name: '${workerFunctionAppName}-code-deploy'
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
        value: workerCodeZipUrl
      }
      {
        name: 'RESOURCE_GROUP'
        value: resourceGroup().name
      }
      {
        name: 'APP_NAME'
        value: workerFunctionAppName
      }
    ]
    scriptContent: codeDeployScriptContent
  }
  dependsOn: [
    workerFunctionAppFlex
    workerFunctionAppClassic
    deployIdentityWebsiteContributor
  ]
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output ingestFunctionAppName string = ingestFunctionAppName
output ingestFunctionAppDefaultHostName string = ingestFunctionApp.properties.defaultHostName
output ingestMessagingEndpoint string = 'https://${ingestFunctionApp.properties.defaultHostName}/api/messages'
output ingestConcurrentRequests int = ingestConcurrentRequests
output ingestMinimumInstanceCount int = ingestMinimumInstanceCount
output ingestMaximumInstanceCount int = ingestMaximumInstanceCount

output workerFunctionAppName string = workerFunctionAppName
output workerFunctionAppDefaultHostName string = workerFunctionAppHostName
output workerHostingPlanType string = workerHostingPlanType
output workerConcurrentRequests int = workerConcurrentRequests
output workerMinimumInstanceCount int = workerMinimumInstanceCount
output workerRequestedMaxInstanceCount int = workerMaximumInstanceCount
// The value actually applied — differs from workerRequestedMaxInstanceCount
// only when workerHostingPlanType is FlexConsumption and the request was
// below the platform's enforced 40-instance floor.
output workerAppliedMaxInstanceCount int = workerHostingPlanType == 'FlexConsumption' ? workerFlexMaximumInstanceCount : workerMaximumInstanceCount

output storageAccountName string = storageAccount.name
output jobQueueName string = jobQueueName
output appInsightsName string = appInsights.name
output keyVaultName string = keyVault.name

output botName string = bot.name
output botAppId string = botIdentity.properties.clientId

output manifestUploaded bool = !empty(manifestSourceBaseUrl)
output manifestBlobUrl string = !empty(manifestSourceBaseUrl) ? '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}/${manifestBlobName}' : ''

output ingestCodeAutoDeployed bool = !empty(ingestCodeZipUrl)
output workerCodeAutoDeployed bool = !empty(workerCodeZipUrl)
