// modules/keyvault.bicep
// Key Vault — RBAC 모드, Function / Logic App / 보안 관리자 권한 부여

param location string
param prefix string
param logAnalyticsWorkspaceId string
param functionIdentityPrincipalId string
param logicappIdentityPrincipalId string
param securityAdminObjectId string

// Key Vault Secrets User role ID (built-in)
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
// Key Vault Administrator role ID (built-in)
var kvAdminRoleId = '00482a5a-887f-4fb3-b363-3b7fe8e74483'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${prefix}'
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Function App — Key Vault Secrets User
resource funcSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionIdentityPrincipalId, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: functionIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Logic App — Key Vault Secrets User
resource laSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, logicappIdentityPrincipalId, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: logicappIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// 보안 관리자 — Key Vault Administrator
resource adminRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, securityAdminObjectId, kvAdminRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvAdminRoleId)
    principalId: securityAdminObjectId
    principalType: 'User'
  }
}

resource kvDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${prefix}-kv'
  scope: keyVault
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'audit'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
