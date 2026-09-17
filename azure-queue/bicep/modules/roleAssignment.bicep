// Resource-group-scoped role assignment, keyed by the principal's actual
// principalId rather than the identity resource's ARM id — see main.bicep's
// comment on botIdentityWebsiteContributor for why. Writing
// guid(..., someIdentity.properties.principalId, ...) directly in a
// resource's `name` at the parent template's scope fails to compile
// (BCP120: a resource name must be computable before deployment starts, and
// principalId is only known once the identity actually deploys). Wrapping
// it in this child module sidesteps that: principalId arrives here as an
// already-resolved parameter value, not a live runtime reference, so it's
// valid to use in this module's own name computation. The practical effect:
// if the source identity is ever deleted and recreated (new principalId,
// same resource name), the next deployment computes a brand-new role
// assignment name here automatically instead of colliding with a stale one
// bound to the old principal — verified by reproducing that exact scenario
// against a disposable test identity.
param principalId string
param roleDefinitionId string
param roleAssignmentNameSeed string
param principalType string = 'ServicePrincipal'

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(roleAssignmentNameSeed, principalId, roleDefinitionId)
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: principalType
  }
}
