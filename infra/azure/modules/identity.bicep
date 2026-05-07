// modules/identity.bicep
// Function App / Logic App 용 User Assigned Managed Identity

param location string
param prefix string

resource functionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-function'
  location: location
}

resource logicappIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-logicapp'
  location: location
}

output functionIdentityId string = functionIdentity.id
output functionIdentityPrincipalId string = functionIdentity.properties.principalId
output functionIdentityClientId string = functionIdentity.properties.clientId

output logicappIdentityId string = logicappIdentity.id
output logicappIdentityPrincipalId string = logicappIdentity.properties.principalId
output logicappIdentityClientId string = logicappIdentity.properties.clientId
