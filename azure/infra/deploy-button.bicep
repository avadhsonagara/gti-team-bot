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
// main.bicep directly via deploy.sh instead.
// =============================================================================

// ---------------------------------------------------------------------------
// Parameters shown on the "Deploy to Azure" form
// ---------------------------------------------------------------------------

@description('Name of the Function App to create.')
param functionAppName string = 'gti-team-bot'

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param gtiApiKey string

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

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Stored as a JSON config blob in the Function App\'s own storage account. Leave empty to use the bot\'s built-in formatting.')
param outputFormatInstructions string = ''

@description('Add RS Alerts to this Team: provisions a second, background Function App that polls Google Threat Intelligence alerts and posts them into a Teams channel. Set to "Yes" to also fill in the two fields below.')
@allowed([
  'No'
  'Yes'
])
param addRsAlerts string = 'No'

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
    gtiApiKey: gtiApiKey
    instanceMemoryMB: instanceMemoryMB
    maximumInstanceCount: maximumInstanceCount
    outputFormatInstructions: outputFormatInstructions
    enableRsAlerts: addRsAlerts == 'Yes'
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
output botName string = main.outputs.botName
output botAppId string = main.outputs.botAppId
output keyVaultName string = main.outputs.keyVaultName
output manifestBlobUrl string = main.outputs.manifestBlobUrl
output rsAlertsEnabled bool = main.outputs.rsAlertsEnabled
output rsAlertsFunctionAppName string = main.outputs.rsAlertsFunctionAppName
