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

// ---------------------------------------------------------------------------
// Ingest Function App
// ---------------------------------------------------------------------------

@description('Name of the Ingest Function App (the messaging endpoint Azure Bot calls).')
param ingestFunctionAppName string = 'gti-teams-bot-ingest'

@description('Maximum concurrent HTTP requests processed per Ingest instance, and the matching Python thread-pool size.')
@minValue(1)
@maxValue(32)
param ingestConcurrentRequests int = 20

@description('Maximum scale-out instance count for the Ingest Function App (functionAppScaleLimit on Consumption plan). Capped at 100 — the real platform ceiling for a Linux Consumption app (Windows gets 200, but this is always Linux) — a value above that is rejected at deployment time.')
@minValue(1)
@maxValue(100)
param ingestMaximumInstanceCount int = 5

// ---------------------------------------------------------------------------
// Worker Function App
// ---------------------------------------------------------------------------

@description('Name of the Worker Function App (runs the actual GTI query).')
param workerFunctionAppName string = 'gti-teams-bot-worker'

@description('Hosting plan for the Worker Function App. Flex Consumption (default): modern serverless with configurable per-instance memory. Consumption: classic scale-to-zero.')
@allowed([
  'Consumption'
  'FlexConsumption'
])
param workerHostingPlanType string = 'FlexConsumption'

@description('Per-instance memory (MB) for the Worker Function App. Ignored when Worker Hosting Plan Type is Consumption. (Only Flex Consumption)')
@allowed([
  512
  2048
  4096
])
param workerInstanceMemoryMB int = 2048

@description('Number of queue messages the Worker Function processes concurrently per instance, and the matching Python thread-pool size.')
@minValue(1)
@maxValue(32)
param workerConcurrentRequests int = 15

@description('Minimum instance count for the Worker Function App. Setting a value > 0 keeps that number of always-ready instances pre-warmed for the queue trigger. Ignored when Worker Hosting Plan Type is Consumption (always scales to zero). (Only Flex Consumption)')
@minValue(0)
@maxValue(100)
param workerMinimumInstanceCount int = 0

@description('Requested maximum scale-out instance count for the Worker Function App. Azure enforces a hard floor of 40 for this on Flex Consumption, and a real ceiling of 100 (Linux app) on Consumption — a value outside either plan\'s range is silently clamped to fit (see the deployment\'s workerAppliedMaxInstanceCount output for what was actually applied).')
@minValue(1)
@maxValue(1000)
param workerMaximumInstanceCount int = 5

// ---------------------------------------------------------------------------
// GTI API Key & Bot Configuration
// ---------------------------------------------------------------------------

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param gtiApiKey string

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Leave empty to use the bot\'s built-in formatting.')
param outputFormatInstructions string = ''

@description('Number of most-recent channel-thread messages to fetch as context for each query. Requires the bot\'s identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent — a manual one-time step. No effect outside channels. Capped at 30 to bound the prompt size sent to GTI.')
@minValue(1)
@maxValue(30)
param threadContextMessageCount int = 5

// ---------------------------------------------------------------------------
// RS Alerts (optional) — a separate, timer-triggered Function App that
// posts new Google Threat Intelligence alerts to a Teams channel. Off by
// default; everything besides these basics (backfill window, hosting
// plan, ...) uses main.bicep's own defaults — use main.bicep directly via
// the CLI for full control over those.
// ---------------------------------------------------------------------------

@description('Set to false to skip provisioning RS Alerts alongside the bot. Enabled by default — Rs Alerts Teams Channel Link Or Id and Rs Alerts Gti Project must be set for it to actually run.')
param enableRsAlerts bool = true

@description('Teams channel link or ID that RS Alerts posts GTI alerts into. Required when Enable Rs Alerts is true.')
param rsAlertsTeamsChannelLinkOrId string = ''

@description('RS Alerts\' GTI project ID, from the Alerts URL (...&project=projects/<id>). Required when Enable Rs Alerts is true.')
param rsAlertsGtiProject string = ''

@description('How often RS Alerts polls GTI for new alerts, in hours — converted internally to a schedule that fires at the top of the hour, every N hours (e.g. 1 -> fires every hour, 6 -> every 6 hours). Default of 1 matches the canonical GTI alerts reference script\'s own hourly cadence.')
@minValue(1)
@maxValue(24)
param rsAlertsPollingIntervalHours int = 1

@description('Filter: Severity level (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value — there is no "disable this dimension" option, matching the canonical GTI alerts reference script.')
param rsAlertsFilterSeverityLevel string = 'MEDIUM,HIGH'

@description('Filter: Priority level (comma-separated LOW/MEDIUM/HIGH/CRITICAL). Must resolve to at least one value.')
param rsAlertsFilterPriorityLevel string = 'MEDIUM,HIGH,CRITICAL'

@description('Filter: Relevance level (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterRelevanceLevel string = 'MEDIUM,HIGH'

@description('Filter: Relevance confidence (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterRelevanceConfidence string = 'MEDIUM,HIGH'

// ---------------------------------------------------------------------------
// Delegate everything else to main.bicep's own defaults
// ---------------------------------------------------------------------------

module main 'main.bicep' = {
  name: 'gti-teams-bot-queue-main'
  params: {
    ingestFunctionAppName: ingestFunctionAppName
    ingestConcurrentRequests: ingestConcurrentRequests
    ingestMaximumInstanceCount: ingestMaximumInstanceCount
    workerFunctionAppName: workerFunctionAppName
    workerHostingPlanType: workerHostingPlanType
    workerInstanceMemoryMB: workerInstanceMemoryMB
    workerConcurrentRequests: workerConcurrentRequests
    workerMinimumInstanceCount: workerMinimumInstanceCount
    workerMaximumInstanceCount: workerMaximumInstanceCount
    gtiApiKey: gtiApiKey
    outputFormatInstructions: outputFormatInstructions
    threadContextMessageCount: threadContextMessageCount
    enableRsAlerts: enableRsAlerts
    rsAlertsTeamsChannelLinkOrId: rsAlertsTeamsChannelLinkOrId
    rsAlertsGtiProject: rsAlertsGtiProject
    rsAlertsPollingIntervalHours: rsAlertsPollingIntervalHours
    rsAlertsFilterSeverityLevel: rsAlertsFilterSeverityLevel
    rsAlertsFilterPriorityLevel: rsAlertsFilterPriorityLevel
    rsAlertsFilterRelevanceLevel: rsAlertsFilterRelevanceLevel
    rsAlertsFilterRelevanceConfidence: rsAlertsFilterRelevanceConfidence
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
output rsAlertsEnabled bool = main.outputs.rsAlertsEnabled
output rsAlertsFunctionAppName string = main.outputs.rsAlertsFunctionAppName
