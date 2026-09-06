// =============================================================================
// Thin front-end template for the "Deploy to Azure" button.
// =============================================================================
// The plain ARM "custom deployment" blade (Microsoft.Template/uri/...) auto-
// generates its form from every parameter in the template, with no way to
// hide ones that already have good defaults — createUiDefinitionUri only
// applies to Managed Application packages, not this blade.
//
// This template exposes only what a first-time deployer actually needs, and
// wraps main.bicep as a module for everything else (storage/plan/Key Vault
// naming, Python version, manifest location, etc.), which keeps its own
// defaults. CLI users who need full control — reusing an existing storage
// account or App Service Plan, custom naming, etc. — should deploy
// main.bicep directly via `az deployment group create` instead.
// =============================================================================

// ---------------------------------------------------------------------------
// Parameters shown on the "Deploy to Azure" form
// ---------------------------------------------------------------------------

@description('Name of the Function App to create.')
param functionAppName string = 'gti-team-bot'

@description('Hosting plan for the Function App. FlexConsumption (default): serverless, scale-to-zero, pay-per-execution. Premium: pre-warmed instances (no cold starts), VNET support.')
@allowed([
  'FlexConsumption'
  'Premium'
])
param hostingPlanType string = 'FlexConsumption'

@description('Function Instance Memory MB: per-instance memory for Flex Consumption. (Only applies when Hosting Plan is FlexConsumption; ignored for Premium).')
@allowed([
  512
  2048
  4096
])
param instanceMemoryMB int = 2048

@description('Premium Plan SKU: compute tier for Premium plan. (Only applies when Hosting Plan is Premium; ignored for FlexConsumption).')
@allowed([
  'EP1'
  'EP2'
  'EP3'
])
param premiumSku string = 'EP1'

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param gtiApiKey string

@description('Maximum scale-out instance count for the Flex Consumption plan. (Ignored for Premium).')
@minValue(40)
@maxValue(1000)
param maximumInstanceCount int = 100

@description('Maximum concurrent HTTP requests processed per instance (Portal: "Scale and concurrency"). Flex Consumption: 0 (default) uses the system-assigned value based on instanceMemoryMB; 1-1000 assigns a fixed value manually. Premium: 0 leaves host.json\'s own value in effect; a nonzero value overrides it at runtime — pick one sized to your own load testing, since Premium has no platform-provided default.')
@minValue(0)
@maxValue(1000)
param httpPerInstanceConcurrency int = 0

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Stored as a JSON config blob in the Function App\'s own storage account. Leave empty to use the bot\'s built-in formatting.')
param outputFormatInstructions string = ''

@description('Number of most-recent channel-thread messages to fetch as context for each query (channel thread context via Microsoft Graph). Requires the bot\'s identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent — a manual one-time step. No effect outside channels.')
@minValue(1)
@maxValue(50)
param threadContextMessageCount int = 5

@description('Add RS Alerts to this Team: provisions a second, background Function App that polls Google Threat Intelligence alerts and posts them into a Teams channel. Set to "Yes" to also fill in the fields below.')
@allowed([
  'No'
  'Yes'
])
param addRsAlerts string = 'No'

@description('Name of the RS Alerts Function App. Only used when "Add RS Alerts to this Team" is Yes.')
param rsAlertsFunctionAppName string = '${functionAppName}-rs-alerts'

@description('Hosting plan for the RS Alerts Function App. Only used when "Add RS Alerts to this Team" is Yes.')
@allowed([
  'FlexConsumption'
  'Premium'
])
param rsAlertsHostingPlanType string = 'FlexConsumption'

@description('Per-instance memory (MB) for RS Alerts Flex Consumption plan. (Only applies when RS Alerts Hosting Plan is FlexConsumption).')
@allowed([
  512
  2048
  4096
])
param rsAlertsInstanceMemoryMB int = 512

@description('Premium plan SKU for RS Alerts. (Only applies when RS Alerts Hosting Plan is Premium).')
@allowed([
  'EP1'
  'EP2'
  'EP3'
])
param rsAlertsPremiumSku string = 'EP1'

@description('The Teams channel RS Alerts posts GTI alerts into. Paste the FULL channel link (right-click the channel -> Get link to channel) — the full link is required so the bot\'s Teams app can be auto-installed into the team via Microsoft Graph. A bare ID (19:xxx@thread.tacv2) still works for delivery, but skips auto-install. Required when "Add RS Alerts to this Team" is Yes.')
param rsAlertsChannelIdOrChannelLink string = ''

@description('GTI project ID for RS Alerts, from the Alerts URL (...&project=projects/<id>). Required when "Add RS Alerts to this Team" is Yes.')
param rsAlertsGtiProject string = ''

@description('RS Alerts filter: Severity level (comma-separated LOW/MEDIUM/HIGH). Empty = no filter on this field.')
param rsAlertsFilterSeverityLevel string = 'MEDIUM,HIGH'

@description('RS Alerts filter: Priority level (comma-separated LOW/MEDIUM/HIGH/CRITICAL). Empty = no filter on this field.')
param rsAlertsFilterPriorityLevel string = 'MEDIUM,HIGH,CRITICAL'

@description('RS Alerts filter: Relevance level (comma-separated LOW/MEDIUM/HIGH). Empty = no filter on this field.')
param rsAlertsFilterRelevanceLevel string = 'MEDIUM,HIGH'

@description('RS Alerts filter: Relevance confidence (comma-separated LOW/MEDIUM/HIGH). Empty = no filter on this field.')
param rsAlertsFilterRelevanceConfidence string = 'MEDIUM,HIGH'

// ---------------------------------------------------------------------------
// Delegate everything else to main.bicep's own defaults
// ---------------------------------------------------------------------------

module main 'main.bicep' = {
  name: 'gti-team-bot-main'
  params: {
    functionAppName: functionAppName
    botName: functionAppName
    hostingPlanType: hostingPlanType
    instanceMemoryMB: instanceMemoryMB
    premiumSku: premiumSku
    gtiApiKey: gtiApiKey
    maximumInstanceCount: maximumInstanceCount
    httpPerInstanceConcurrency: httpPerInstanceConcurrency
    outputFormatInstructions: outputFormatInstructions
    threadContextMessageCount: threadContextMessageCount
    enableRsAlerts: addRsAlerts == 'Yes'
    rsAlertsFunctionAppName: rsAlertsFunctionAppName
    rsAlertsHostingPlanType: rsAlertsHostingPlanType
    rsAlertsInstanceMemoryMB: rsAlertsInstanceMemoryMB
    rsAlertsPremiumSku: rsAlertsPremiumSku
    rsAlertsTeamsChannelId: rsAlertsChannelIdOrChannelLink
    rsAlertsGtiProject: rsAlertsGtiProject
    rsAlertsFilterSeverityLevel: rsAlertsFilterSeverityLevel
    rsAlertsFilterPriorityLevel: rsAlertsFilterPriorityLevel
    rsAlertsFilterRelevanceLevel: rsAlertsFilterRelevanceLevel
    rsAlertsFilterRelevanceConfidence: rsAlertsFilterRelevanceConfidence
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output functionAppName string = main.outputs.functionAppName
output functionAppMessagingEndpoint string = main.outputs.functionAppMessagingEndpoint
output hostingPlanType string = main.outputs.hostingPlanType
output botName string = main.outputs.botName
output botAppId string = main.outputs.botAppId
output keyVaultName string = main.outputs.keyVaultName
output manifestBlobUrl string = main.outputs.manifestBlobUrl
output rsAlertsEnabled bool = main.outputs.rsAlertsEnabled
output rsAlertsFunctionAppName string = main.outputs.rsAlertsFunctionAppName
output rsAlertsHostingPlanType string = main.outputs.rsAlertsHostingPlanType
