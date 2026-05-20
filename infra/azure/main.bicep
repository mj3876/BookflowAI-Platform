// main.bicep
// 전체 Azure 인프라 오케스트레이션
// VPN Connection: AWS TGW 구축 후 awsTgwActiveIp 등 채워서 같이 배포

targetScope = 'resourceGroup'

// ── 파라미터 ──────────────────────────────────────────────
param environment string
param location string
param prefix string

param vnetAddressPrefix string
param gatewaySubnetPrefix string
param servicesSubnetPrefix string
param functionSubnetPrefix string

param vpnBgpAsn int
param logRetentionDays int

param securityAdminObjectId string

// ── AWS Cross-Cloud VPN 파라미터 (선택) ─────────────────────
// AWS TGW 의 VPN attachment 가 만든 첫 번째 tunnel public IP + inside CIDR 의 customer side IP + PSK
// 빈 문자열이면 vpn-connection 모듈 skip (Phase 1-3) · 채워지면 deploy (Phase 4+)
param awsTgwActiveIp string = ''
param awsTgwBgpPeeringIp string = ''
@secure()
param vpnPreSharedKey string = ''
// AWS APIPA inside CIDR 의 customer side IP (예: 169.254.21.6) — VPN GW 의 customBgpIpAddresses 로 사용
param vpnCustomBgpIpAddresses array = []

// ── 1. 관리 ID ────────────────────────────────────────────
module identity 'modules/identity.bicep' = {
  name: 'identity-deploy'
  params: {
    location: location
    prefix: prefix
  }
}

// ── 2. NSG ────────────────────────────────────────────────
module nsg 'modules/nsg.bicep' = {
  name: 'nsg-deploy'
  params: {
    location: location
    prefix: prefix
  }
}

// ── 3. VNet (NSG 참조) ────────────────────────────────────
module vnet 'modules/vnet.bicep' = {
  name: 'vnet-deploy'
  dependsOn: [nsg]
  params: {
    location: location
    prefix: prefix
    vnetAddressPrefix: vnetAddressPrefix
    gatewaySubnetPrefix: gatewaySubnetPrefix
    servicesSubnetPrefix: servicesSubnetPrefix
    functionSubnetPrefix: functionSubnetPrefix
    servicesNsgId: nsg.outputs.servicesNsgId
    functionNsgId: nsg.outputs.functionNsgId
  }
}

// ── 4. Log Analytics (Monitor) ────────────────────────────
module monitor 'modules/monitor.bicep' = {
  name: 'monitor-deploy'
  params: {
    location: location
    prefix: prefix
    logRetentionDays: logRetentionDays
  }
}

// ── 5. Key Vault (관리 ID, Monitor 참조) ─────────────────
module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault-deploy'
  dependsOn: [identity, monitor]
  params: {
    location: location
    prefix: prefix
    logAnalyticsWorkspaceId: monitor.outputs.workspaceId
    functionIdentityPrincipalId: identity.outputs.functionIdentityPrincipalId
    logicappIdentityPrincipalId: identity.outputs.logicappIdentityPrincipalId
    securityAdminObjectId: securityAdminObjectId
  }
}

// ── 6. Function App (VNet, Key Vault, 관리 ID 참조) ───────
module function 'modules/function.bicep' = {
  name: 'function-deploy'
  dependsOn: [keyvault, identity]
  params: {
    location: location
    prefix: prefix
    keyVaultUri: keyvault.outputs.keyVaultUri
    functionIdentityId: identity.outputs.functionIdentityId
    functionIdentityClientId: identity.outputs.functionIdentityClientId
    logAnalyticsWorkspaceId: monitor.outputs.workspaceId
  }
}

// ── 7. Event Grid (Key Vault, Function App 참조) ──────────
module eventgrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid-deploy'
  dependsOn: [keyvault, function]
  params: {
    location: location
    prefix: prefix
    keyVaultId: keyvault.outputs.keyVaultId
  }
}

// ── 수신자 파라미터 (JSON 배열 문자열) ──────────────────────
// 형식: '[{"address":"a@b.com","displayName":"이름"},...]'
param digestRecipients string   // 일일 요약·계획 완료: 본사+경영진+WH+지점 전체
param dashboardBaseUrl string = 'https://bookflow.duckdns.org'

// ── 8. ACS Email ──────────────────────────────────────────
module acs 'modules/acs.bicep' = {
  name: 'acs-deploy'
  dependsOn: [identity]
  params: {
    prefix: prefix
    logicappIdentityPrincipalId: identity.outputs.logicappIdentityPrincipalId
  }
}

// ── 9-1. Logic Apps Standard (관리 ID, Key Vault, ACS, VNet 참조) ────────
module logicapp 'modules/logicapp.bicep' = {
  name: 'logicapp-deploy'
  dependsOn: [identity, keyvault, acs, vnet]
  params: {
    location: location
    prefix: prefix
    logicappIdentityId: identity.outputs.logicappIdentityId
    logicappIdentityClientId: identity.outputs.logicappIdentityClientId
    keyVaultUri: keyvault.outputs.keyVaultUri
    logAnalyticsWorkspaceId: monitor.outputs.workspaceId
    acsEndpoint: acs.outputs.acsEndpoint
    acsSenderAddress: acs.outputs.acsSenderAddress
    digestRecipients: digestRecipients
    dashboardBaseUrl: dashboardBaseUrl
    functionSubnetId: vnet.outputs.functionSubnetId
    servicesSubnetId: vnet.outputs.servicesSubnetId
    vnetId: vnet.outputs.vnetId
  }
}

// ── 9-1b. Logic Apps Consumption diagnosticSettings (3개 워크플로 · Portal 생성분) ──
// approval-request / stock-depart / stock-arrival — 워크플로 본체는 Portal 관리,
// 진단 설정만 IaC 로 보장 (law-bookflowmj 라우팅 누락 시 대시보드에서 안 보이는 문제 방지).
module logicappConsumptionDiag 'modules/logicapp-consumption-diag.bicep' = {
  name: 'logicapp-consumption-diag-deploy'
  dependsOn: [monitor]
  params: {
    logAnalyticsWorkspaceId: monitor.outputs.workspaceId
  }
}

// ── 9-2. VPN Gateway (VNet 참조) ──
module vpn 'modules/vpn.bicep' = {
  name: 'vpn-deploy'
  dependsOn: [vnet]
  params: {
    location: location
    prefix: prefix
    gatewaySubnetId: vnet.outputs.gatewaySubnetId
    vpnBgpAsn: vpnBgpAsn
    customBgpIpAddresses: vpnCustomBgpIpAddresses
  }
}

// ── 10. VPN Connection (선택 · AWS Tunnel IP 채워질 때만 deploy) ──
module vpnConnection 'modules/vpn-connection.bicep' = if (!empty(awsTgwActiveIp)) {
  name: 'vpn-connection-deploy'
  dependsOn: [vpn]
  params: {
    prefix: prefix
    vpnGatewayName: 'vpngw-${prefix}'
    awsTgwActiveIp: awsTgwActiveIp
    awsTgwBgpPeeringIp: awsTgwBgpPeeringIp
    preSharedKey: vpnPreSharedKey
  }
}

// ── 출력값 ───────────────────────────────────────────────
output vnetId string = vnet.outputs.vnetId
output keyVaultName string = keyvault.outputs.keyVaultName
output keyVaultUri string = keyvault.outputs.keyVaultUri
output functionAppName string = function.outputs.functionAppName

// AWS 팀에 전달할 값
output vpnActivePublicIp string = vpn.outputs.activePublicIp
output vpnStandbyPublicIp string = vpn.outputs.standbyPublicIp
output vpnBgpPeeringAddress string = vpn.outputs.bgpPeeringAddress
output vpnBgpAsn int = vpn.outputs.bgpAsn
