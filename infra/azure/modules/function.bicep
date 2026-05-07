// modules/function.bicep
// Storage Account + Consumption Plan + Function App

param location string
param prefix string
param keyVaultUri string
param functionIdentityId string
param functionIdentityClientId string
param logAnalyticsWorkspaceId string

// Storage Account 이름: 소문자·숫자만, 최대 24자
var storageAccountName = 'st${replace(prefix, '-', '')}func'

// ── Storage Account ───────────────────────────────────────
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ── App Service Plan (Consumption) ───────────────────────
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: 'asp-${prefix}'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true  // Linux
  }
}

// ── Function App ──────────────────────────────────────────
resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: 'func-${prefix}-sync'
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${functionIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      pythonVersion: '3.11'

      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        // 관리 ID Client ID 지정 (여러 관리 ID 사용 시 명시 필요)
        {
          name: 'AZURE_CLIENT_ID'
          value: functionIdentityClientId
        }
        // Key Vault 참조로 시크릿 주입
        {
          name: 'KEY_VAULT_URI'
          value: keyVaultUri
        }
        // VPN 연결 완료 후 Key Vault 참조로 교체 예정 (미설정 시 앱 시작 실패 방지)
        {
          name: 'AWS_API_GATEWAY_URL'
          value: 'placeholder'
        }
      ]
    }
  }
}

// ── Function App 진단 설정 ────────────────────────────────
resource functionDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${prefix}-func'
  scope: functionApp
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'FunctionAppLogs'
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

// ── 출력값 ───────────────────────────────────────────────
output functionAppId string = functionApp.id
output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
