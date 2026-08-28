// =============================================================================
// GTI Teams Bot (Agentic) — Azure Functions infrastructure
// =============================================================================
// Source: https://github.com/avadhsonagara/gti-team-bot
//
// Provisions a Flex Consumption Python Function App (matching the existing
// "gti-team-bot" app: kind functionapp,linux, Flex Consumption plan), wires
// up Application Insights + deployment storage, applies the app's runtime
// configuration as app settings, and uploads the Teams app manifest package
// (manifest.json + icons, prebuilt as teams-app-manifest/teams-app-manifest.zip
// in this repo) to a blob container in the same storage account.
//
// The manifest zip is fetched at deploy time from manifestZipUrl (defaults to
// this repo's raw GitHub URL) rather than passed as inline content — this is
// what makes the template usable from the "Deploy to Azure" button, where the
// caller is filling out a portal form rather than running a local script.
// =============================================================================

@description('Name of the Function App to create.')
param functionAppName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Globally-unique Storage Account name (3-24 lowercase alphanumeric characters).')
@minLength(3)
@maxLength(24)
param storageAccountName string = toLower('${take(replace(functionAppName, '-', ''), 11)}${uniqueString(resourceGroup().id, functionAppName)}')

@description('Name of the Application Insights resource.')
param appInsightsName string = '${functionAppName}-insights'

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

@description('Name of the Flex Consumption App Service Plan to use.')
param appServicePlanName string = '${functionAppName}-plan'

@description('Set to false to reuse an existing App Service Plan named appServicePlanName in this resource group, instead of creating a new one. A Function App cannot be moved between Flex Consumption plans in-place, so this must be false when redeploying onto an app that already exists on a different plan.')
param createAppServicePlan bool = true

@description('Non-secret application settings merged onto the Function App (e.g. GTI_API_BASE_URL, LOG_FORMAT).')
param appSettings object = {}

@description('Microsoft Entra app (Bot Framework registration) client ID.')
param clientId string

@description('Microsoft Entra tenant ID for the Bot Framework registration. Defaults to the deployment\'s own tenant, which is where this app registration lives for the vast majority of setups; override if yours differs.')
param tenantId string = subscription().tenantId

@description('Client secret value for the Bot Framework registration. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param clientSecret string

@description('Google Threat Intelligence Agentic API key. Stored as a Key Vault secret, never as a plaintext app setting.')
@secure()
param gtiApiKey string

@description('Globally-unique Key Vault name (3-24 characters) used to store clientSecret and gtiApiKey.')
@minLength(3)
@maxLength(24)
param keyVaultName string = toLower('kv-${take(replace(functionAppName, '-', ''), 9)}-${take(uniqueString(resourceGroup().id, functionAppName), 9)}')

@description('Blob container that receives the Teams app manifest package.')
param manifestContainerName string = 'teams-manifest'

@description('Blob name for the uploaded Teams app manifest zip.')
param manifestBlobName string = 'teams-app-manifest.zip'

@description('URL the Teams app manifest zip is fetched from at deploy time. Defaults to this repo\'s prebuilt package. Skip the upload by leaving this empty.')
param manifestZipUrl string = 'https://raw.githubusercontent.com/avadhsonagara/gti-team-bot/main/teams-app-manifest/teams-app-manifest.zip'

@description('Tags applied to all resources.')
param tags object = {}

@description('Forces the manifest-upload deployment script to re-run on every deployment. Microsoft.Resources/deploymentScripts otherwise skips re-execution — and keeps its old environment variables (e.g. a stale storage account name) — when redeployed without this changing.')
param forceUpdateTag string = utcNow()

var deploymentContainerName = 'app-package-${toLower(functionAppName)}'
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

// Kept separate from the built-in (storage/App Insights) settings below because those are derived
// from listKeys(), which cannot be referenced from inside a for-expression.
var customAppSettingsArray = [for key in items(appSettings): {
  name: key.key
  value: key.value
}]

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

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource manifestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: manifestContainerName
  properties: {
    publicAccess: 'None'
  }
}

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

resource kvSecretClientSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'ClientSecret'
  properties: {
    value: clientSecret
  }
}

resource kvSecretGtiApiKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GtiApiKey'
  properties: {
    value: gtiApiKey
  }
}

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

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: createAppServicePlan ? appServicePlan.id : existingAppServicePlan.id
    httpsOnly: true
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
          name: 'CLIENT_ID'
          value: clientId
        }
        {
          name: 'TENANT_ID'
          value: tenantId
        }
        {
          name: 'CLIENT_SECRET'
          value: '@Microsoft.KeyVault(SecretUri=${kvSecretClientSecret.properties.secretUri})'
        }
        {
          name: 'GTI_API_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${kvSecretGtiApiKey.properties.secretUri})'
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

resource kvSecretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    // Built-in "Key Vault Secrets User" role — read-only access to secret values.
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource manifestUpload 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (!empty(manifestZipUrl)) {
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
        name: 'ZIP_URL'
        value: manifestZipUrl
      }
    ]
    scriptContent: '''
      set -e
      curl -fsSL "$ZIP_URL" -o /tmp/manifest.zip
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

output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output functionAppMessagingEndpoint string = 'https://${functionApp.properties.defaultHostName}/api/messages'
output storageAccountName string = storageAccount.name
output appInsightsName string = appInsights.name
output keyVaultName string = keyVault.name
output manifestContainerUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}'
output manifestBlobUrl string = '${storageAccount.properties.primaryEndpoints.blob}${manifestContainerName}/${manifestBlobName}'
