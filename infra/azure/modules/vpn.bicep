// modules/vpn.bicep
// VPN Gateway 뼈대 — 공인 IP 확보 및 Gateway 생성까지만
// VPN Connection 은 AWS TGW 구축 완료 후 vpn-connection.bicep 에서 별도 배포

param location string
param prefix string
param gatewaySubnetId string
param vpnBgpAsn int

// AWS APIPA (169.254.x.x/30) BGP peering 을 위한 customBgpIpAddresses.
// 빈 배열이면 default (VNet subnet IP). AWS-Azure VPN 시 AWS 의 inside CIDR 의 customer side IP 를 명시.
// 예: '169.254.21.6' (AWS Tunnel 1 inside CIDR 169.254.21.4/30 의 customer side)
param customBgpIpAddresses array = []

// ── 퍼블릭 IP (Active) ────────────────────────────────────
resource pipActive 'Microsoft.Network/publicIPAddresses@2023-05-01' = {
  name: 'pip-${prefix}-vpngw-active'
  location: location
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  zones: ['1', '2', '3']
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

// ── 퍼블릭 IP (Standby) ───────────────────────────────────
resource pipStandby 'Microsoft.Network/publicIPAddresses@2023-05-01' = {
  name: 'pip-${prefix}-vpngw-standby'
  location: location
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  zones: ['1', '2', '3']
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

// ── VPN Gateway ───────────────────────────────────────────
// 생성 30~45분 소요
resource vpnGateway 'Microsoft.Network/virtualNetworkGateways@2023-05-01' = {
  name: 'vpngw-${prefix}'
  location: location
  properties: {
    gatewayType: 'Vpn'
    vpnType: 'RouteBased'
    sku: {
      name: 'VpnGw1AZ'
      tier: 'VpnGw1AZ'
    }

    // BGP 활성화 · AWS APIPA peering 시 customBgpIpAddresses 명시 (없으면 VNet subnet default)
    enableBgp: true
    bgpSettings: empty(customBgpIpAddresses) ? {
      asn: vpnBgpAsn
    } : {
      asn: vpnBgpAsn
      bgpPeeringAddresses: [
        {
          ipconfigurationId: '${resourceId('Microsoft.Network/virtualNetworkGateways', 'vpngw-${prefix}')}/ipConfigurations/ipconfig-active'
          customBgpIpAddresses: customBgpIpAddresses
        }
      ]
    }

    // Active/Standby 구성
    activeActive: false

    ipConfigurations: [
      {
        name: 'ipconfig-active'
        properties: {
          publicIPAddress: {
            id: pipActive.id
          }
          subnet: {
            id: gatewaySubnetId
          }
        }
      }
    ]
  }
}

// ── 출력값 ───────────────────────────────────────────────
// 이 값들을 AWS 팀에 전달해서 Customer Gateway 등록에 사용
output vpnGatewayId string = vpnGateway.id
output vpnGatewayName string = vpnGateway.name
output activePublicIp string = pipActive.properties.ipAddress
output standbyPublicIp string = pipStandby.properties.ipAddress
output bgpPeeringAddress string = vpnGateway.properties.bgpSettings.bgpPeeringAddress
output bgpAsn int = vpnGateway.properties.bgpSettings.asn
