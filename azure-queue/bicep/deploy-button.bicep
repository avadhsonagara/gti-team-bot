// =============================================================================
// Thin front-end template for the "Deploy to Azure" button.
// =============================================================================
// The plain ARM "custom deployment" blade auto-generates its form from every
// parameter in the template, with no way to hide ones that already have good
// defaults. This template exposes only what a first-time deployer actually
// needs, and wraps main.bicep as a module for everything else. CLI users who
// need full control — reusing an existing storage account or App Service
// Plan, custom naming, code/manifest auto-deploy, etc. — should deploy
// main.bicep directly via `az deployment group create` instead.
// =============================================================================

@description('Name of the Ingest Function App (the messaging endpoint Azure Bot calls).')
param ingestFunctionAppName string = 'gti-teams-bot-ingest'

@description('Name of the Worker Function App (runs the actual GTI query).')
param workerFunctionAppName string = 'gti-teams-bot-worker'

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param gtiApiKey string

@description('Hosting plan for the Worker Function App. Flex Consumption (default): modern serverless with configurable per-instance memory. Consumption: classic scale-to-zero.')
@allowed([
  'Consumption'
  'FlexConsumption'
])
param workerHostingPlanType string = 'FlexConsumption'

@description('Worker Instance Memory MB: per-instance memory for Flex Consumption. (Only applies when Worker Hosting Plan is FlexConsumption; ignored for Consumption).')
@allowed([
  512
  2048
  4096
])
param workerInstanceMemoryMB int = 2048

@description('Requested maximum scale-out instance count for the Worker Function App. Azure enforces a hard floor of 40 for this on Flex Consumption specifically — a lower value here is silently raised to 40 only on that plan type (see the deployment\'s workerAppliedMaxInstanceCount output for what was actually applied).')
@minValue(1)
@maxValue(1000)
param workerMaximumInstanceCount int = 5

@description('Number of queue messages the Worker Function processes concurrently per instance, and the matching Python thread-pool size.')
@minValue(1)
@maxValue(32)
param workerConcurrentRequests int = 15

@description('Maximum concurrent HTTP requests processed per Ingest instance, and the matching Python thread-pool size.')
@minValue(1)
@maxValue(32)
param ingestConcurrentRequests int = 20

@description('Client-side read timeout (seconds) for a single GTI Agentic API call.')
@minValue(30)
@maxValue(1800)
param gtiTimeoutSeconds int = 480

@description('A dequeued job older than this (seconds) is treated as stale and dropped with an apology instead of running an expensive GTI query for it.')
@minValue(60)
@maxValue(1800)
param maxJobAgeSeconds int = 480

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Leave empty to use the bot\'s built-in formatting.')
param outputFormatInstructions string = ''

@description('Number of most-recent channel-thread messages to fetch as context for each query. Requires the bot\'s identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent — a manual one-time step. No effect outside channels.')
@minValue(1)
@maxValue(50)
param threadContextMessageCount int = 5

// ---------------------------------------------------------------------------
// Delegate everything else to main.bicep's own defaults
// ---------------------------------------------------------------------------

module main 'main.bicep' = {
  name: 'gti-teams-bot-queue-main'
  params: {
    ingestFunctionAppName: ingestFunctionAppName
    workerFunctionAppName: workerFunctionAppName
    gtiApiKey: gtiApiKey
    workerHostingPlanType: workerHostingPlanType
    workerInstanceMemoryMB: workerInstanceMemoryMB
    workerMaximumInstanceCount: workerMaximumInstanceCount
    workerConcurrentRequests: workerConcurrentRequests
    ingestConcurrentRequests: ingestConcurrentRequests
    gtiTimeoutSeconds: gtiTimeoutSeconds
    maxJobAgeSeconds: maxJobAgeSeconds
    outputFormatInstructions: outputFormatInstructions
    threadContextMessageCount: threadContextMessageCount
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output ingestFunctionAppName string = main.outputs.ingestFunctionAppName
output ingestMessagingEndpoint string = main.outputs.ingestMessagingEndpoint
output workerFunctionAppName string = main.outputs.workerFunctionAppName
output workerHostingPlanType string = main.outputs.workerHostingPlanType
output workerAppliedMaxInstanceCount int = main.outputs.workerAppliedMaxInstanceCount
output botName string = main.outputs.botName
output botAppId string = main.outputs.botAppId
output keyVaultName string = main.outputs.keyVaultName
output storageAccountName string = main.outputs.storageAccountName
