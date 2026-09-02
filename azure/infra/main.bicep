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
//
// Two ways to deploy this template:
//   - infra/deploy-button.bicep: a thin wrapper exposing only functionAppName /
//     gtiApiKey / instanceMemoryMB / maximumInstanceCount, used by the
//     "Deploy to Azure" button in the README.
//   - infra/deploy.sh: full CLI control over every parameter below (custom
//     naming, reusing an existing storage account or App Service Plan, etc.).
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

@description('Name of the Flex Consumption App Service Plan to use.')
param appServicePlanName string = '${functionAppName}-plan'

@description('Set to false to reuse an existing App Service Plan named appServicePlanName in this resource group, instead of creating a new one. A Function App cannot be moved between Flex Consumption plans in-place, so this must be false when redeploying onto an app that already exists on a different plan.')
param createAppServicePlan bool = true

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

@description('Non-secret application settings merged onto the Function App (e.g. GTI_API_BASE_URL).')
param appSettings object = {}

@description('Optional formatting instructions applied to every bot response (e.g. "Show severity as bold text instead of emoji"). Seeds a JSON config blob (bot-config/output-format.json) in the Function App\'s own storage account on first read — after that the blob is the source of truth and this value is ignored. Leave empty to use the built-in formatting from app/gti/prompt.md.')
param outputFormatInstructions string = ''

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
param manifestSourceBaseUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/azure/gti-bot/teams-app-manifest'

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

@description('Name of the RS Alerts Flex Consumption App Service Plan. Only used when enableRsAlerts is true.')
param rsAlertsAppServicePlanName string = '${rsAlertsFunctionAppName}-plan'

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

var rsAlertsDeploymentContainerName = 'app-package-${toLower(rsAlertsFunctionAppName)}'
var rsAlertsStateContainerName = 'rs-alerts-state'
var rsAlertsCustomAppSettingsArray = [for key in items(rsAlertsAppSettings): {
  name: key.key
  value: key.value
}]

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
// App Service Plan (Flex Consumption)
// ---------------------------------------------------------------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = if (createAppServicePlan) {
  name: appServicePlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
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

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
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
      appSettings: concat([
        {
          name: 'AzureWebJobsStorage'
          value: storageConnectionString
        }
        {
          name: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          value: storageConnectionString
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          // The Teams SDK (microsoft_teams.apps.App) reads CLIENT_ID +
          // MANAGED_IDENTITY_CLIENT_ID together to authenticate via managed
          // identity instead of a client secret — see app/teams/bot.py.
          name: 'CLIENT_ID'
          value: botIdentity.properties.clientId
        }
        {
          name: 'TENANT_ID'
          value: tenantId
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
      scaleAndConcurrency: {
        maximumInstanceCount: maximumInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }
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
    endpoint: 'https://${functionApp.properties.defaultHostName}/api/messages'
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
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource rsAlertsFunctionApp 'Microsoft.Web/sites@2023-12-01' = if (enableRsAlerts) {
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
      appSettings: concat([
        {
          name: 'AzureWebJobsStorage'
          value: storageConnectionString
        }
        {
          name: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          value: storageConnectionString
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          // Same User-Assigned Managed Identity as the main bot — its client
          // ID is also the Azure Bot's msaAppId, so RS Alerts authenticates
          // to the Bot Framework Connector API (and Microsoft Graph, for
          // Teams app auto-install) as the same bot (app/bot_auth.py,
          // app/graph_client.py).
          name: 'CLIENT_ID'
          value: botIdentity.properties.clientId
        }
        {
          name: 'TENANT_ID'
          value: tenantId
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

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output functionAppMessagingEndpoint string = 'https://${functionApp.properties.defaultHostName}/api/messages'
output storageAccountName string = storageAccount.name
output appInsightsName string = appInsights.name
output keyVaultName string = keyVault.name
output botName string = bot.name
output botAppId string = botIdentity.properties.clientId
output manifestContainerUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}'
output manifestBlobUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}/${manifestBlobName}'

output rsAlertsEnabled bool = enableRsAlerts
output rsAlertsFunctionAppName string = enableRsAlerts ? rsAlertsFunctionApp.name : ''
output rsAlertsFunctionAppDefaultHostName string = enableRsAlerts ? rsAlertsFunctionApp!.properties.defaultHostName : ''
