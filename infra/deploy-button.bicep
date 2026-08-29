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

@description('Azure region for all resources. Defaults to the resource group\'s own recorded location — override this if that differs from where you actually want resources placed (e.g. redeploying into a resource group that already has same-named resources in a different region).')
param location string = resourceGroup().location

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

// ---------------------------------------------------------------------------
// Delegate everything else to main.bicep's own defaults
// ---------------------------------------------------------------------------

module main 'main.bicep' = {
  name: 'gti-team-bot-main'
  params: {
    functionAppName: functionAppName
    location: location
    gtiApiKey: gtiApiKey
    instanceMemoryMB: instanceMemoryMB
    maximumInstanceCount: maximumInstanceCount
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
