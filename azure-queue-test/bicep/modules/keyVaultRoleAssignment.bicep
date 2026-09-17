// Key Vault-scoped role assignment, keyed by the principal's actual
// principalId — see modules/roleAssignment.bicep's comment for the full
// story on why this needs to be a child module at all (BCP120).
//
// A module's own `scope:` can only target a deployment scope (resourceGroup/
// subscription/managementGroup/tenant), not an arbitrary resource like a
// Key Vault (confirmed: Bicep rejects that with BCP134) — so this module
// still deploys at the parent's resource-group scope, and instead scopes
// the role assignment RESOURCE itself to an `existing` reference to the
// vault, looked up by name.
param keyVaultName string
param principalId string
param roleDefinitionId string
param principalType string = 'ServicePrincipal'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, roleDefinitionId)
  scope: keyVault
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: principalType
  }
}
