// =============================================================================
// Thin front-end template for the "Deploy to Azure" button.
// =============================================================================
// Same reasoning as ../../azure/infra/deploy-button.bicep: the plain ARM
// "custom deployment" blade auto-generates its form from every parameter in
// the template, with no way to hide ones that already have good defaults.
// This template exposes only what a first-time deployer actually needs, and
// wraps main.bicep as a module for everything else. CLI users who need full
// control should deploy main.bicep directly via `az deployment group create`.
// =============================================================================

@description('Name of the Container App to create (the interactive bot).')
param containerAppName string = 'gti-teams-bot-agentic'

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext env var.')
@secure()
param gtiApiKey string

@description('Dedicated workload profile SKU the environment\'s ingress (and the bot itself) runs on — this is what unlocks Premium ingress\'s configurable idle timeout. D-series are general-purpose (balanced CPU/memory).')
@allowed([
  'D4'
  'D8'
  'D16'
  'D32'
])
param dedicatedWorkloadProfileType string = 'D4'

@description('Idle request timeout (minutes) for Premium ingress. Must comfortably exceed GTI Timeout Seconds below. Valid range is 4-30 minutes (Azure Container Apps\' own hard ceiling).')
@minValue(4)
@maxValue(30)
param premiumIngressIdleTimeoutMinutes int = 15

@description('Client-side read timeout (seconds) for a single GTI Agentic API call.')
@minValue(30)
@maxValue(1800)
param gtiTimeoutSeconds int = 600

@description('Minimum bot replicas.')
@minValue(0)
@maxValue(300)
param botMinReplicas int = 1

@description('Maximum bot replicas.')
@minValue(1)
@maxValue(300)
param botMaxReplicas int = 10

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Leave empty to use the bot\'s built-in formatting.')
param outputFormatInstructions string = ''

@description('Number of most-recent channel-thread messages to fetch as context for each query. Requires the bot\'s identity to be granted the Graph APPLICATION permission ChannelMessage.Read.All with tenant-admin consent — a manual one-time step. No effect outside channels.')
@minValue(1)
@maxValue(50)
param threadContextMessageCount int = 5

@description('Add RS Alerts to this Team: provisions a background Function App that polls Google Threat Intelligence alerts and posts them into a Teams channel. Set to "Yes" to also fill in the fields below.')
@allowed([
  'No'
  'Yes'
])
param addRsAlerts string = 'Yes'

@description('Name of the RS Alerts Function App. Only used when "Add RS Alerts to this Team" is Yes.')
param rsAlertsFunctionAppName string = '${containerAppName}-rs-alerts'

@description('Hosting plan for the RS Alerts Function App — Consumption or Flex Consumption only (Premium is not offered here).')
@allowed([
  'Consumption'
  'FlexConsumption'
])
param rsAlertsHostingPlanType string = 'Consumption'

@description('Per-instance memory (MB) for the RS Alerts Flex Consumption plan. (Only applies when RS Alerts Hosting Plan is Flex Consumption).')
@allowed([
  512
  2048
  4096
])
param rsAlertsInstanceMemoryMB int = 512

@description('The Teams channel RS Alerts posts GTI alerts into. Paste the FULL channel link (right-click the channel -> Get link to channel) — the full link is required so the bot\'s Teams app can be auto-installed into the team via Microsoft Graph. A bare ID (19:xxx@thread.tacv2) still works for delivery, but skips auto-install. Required when "Add RS Alerts to this Team" is Yes.')
param rsAlertsChannelIdOrChannelLink string = ''

@description('RS Alert Project ID: the GTI project RS Alerts polls for alerts, from the Alerts URL (...&project=projects/<id>). Required when "Add RS Alerts to this Team" is Yes.')
param rsAlertsGtiProject string = ''

@description('Backfill window (days) used to seed RS Alerts\' cursor on its very first run, instead of the project\'s entire history.')
@minValue(1)
@maxValue(7)
param rsAlertsBackfillDays int = 7

@description('RS Alerts filter: Severity level (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterSeverityLevel string = 'MEDIUM,HIGH'

@description('RS Alerts filter: Priority level (comma-separated LOW/MEDIUM/HIGH/CRITICAL). Must resolve to at least one value.')
param rsAlertsFilterPriorityLevel string = 'MEDIUM,HIGH,CRITICAL'

@description('RS Alerts filter: Relevance level (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterRelevanceLevel string = 'MEDIUM,HIGH'

@description('RS Alerts filter: Relevance confidence (comma-separated LOW/MEDIUM/HIGH). Must resolve to at least one value.')
param rsAlertsFilterRelevanceConfidence string = 'MEDIUM,HIGH'

// ---------------------------------------------------------------------------
// Delegate everything else to main.bicep's own defaults
// ---------------------------------------------------------------------------

module main 'main.bicep' = {
  name: 'gti-teams-bot-container-main'
  params: {
    containerAppName: containerAppName
    gtiApiKey: gtiApiKey
    dedicatedWorkloadProfileType: dedicatedWorkloadProfileType
    premiumIngressIdleTimeoutMinutes: premiumIngressIdleTimeoutMinutes
    gtiTimeoutSeconds: gtiTimeoutSeconds
    botMinReplicas: botMinReplicas
    botMaxReplicas: botMaxReplicas
    outputFormatInstructions: outputFormatInstructions
    threadContextMessageCount: threadContextMessageCount
    enableRsAlerts: addRsAlerts == 'Yes'
    rsAlertsFunctionAppName: rsAlertsFunctionAppName
    rsAlertsHostingPlanType: rsAlertsHostingPlanType
    rsAlertsInstanceMemoryMB: rsAlertsInstanceMemoryMB
    rsAlertsTeamsChannelId: rsAlertsChannelIdOrChannelLink
    rsAlertsGtiProject: rsAlertsGtiProject
    rsAlertsBackfillDays: rsAlertsBackfillDays
    rsAlertsFilterSeverityLevel: rsAlertsFilterSeverityLevel
    rsAlertsFilterPriorityLevel: rsAlertsFilterPriorityLevel
    rsAlertsFilterRelevanceLevel: rsAlertsFilterRelevanceLevel
    rsAlertsFilterRelevanceConfidence: rsAlertsFilterRelevanceConfidence
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output containerAppName string = main.outputs.containerAppName
output botMessagingEndpoint string = main.outputs.botMessagingEndpoint
output botName string = main.outputs.botName
output botAppId string = main.outputs.botAppId
output botDockerHubRepository string = main.outputs.botDockerHubRepository
output keyVaultName string = main.outputs.keyVaultName
output manifestBlobUrl string = main.outputs.manifestBlobUrl
output rsAlertsEnabled bool = main.outputs.rsAlertsEnabled
output rsAlertsFunctionAppName string = main.outputs.rsAlertsFunctionAppName
output rsAlertsHostingPlanType string = main.outputs.rsAlertsHostingPlanType
