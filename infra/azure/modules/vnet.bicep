// modules/vnet.bicep
// VNet + GatewaySubnet / services / function 서브넷

param location string
param prefix string
param vnetAddressPrefix string
param gatewaySubnetPrefix string
param servicesSubnetPrefix string
param functionSubnetPrefix string
param servicesNsgId string
param functionNsgId string

resource vnet 'Microsoft.Network/virtualNetworks@2023-05-01' = {
  name: 'vnet-${prefix}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        // VPN Gateway 전용 서브넷 — NSG 연결 불가
        name: 'GatewaySubnet'
        properties: {
          addressPrefix: gatewaySubnetPrefix
        }
      }
      {
        name: 'snet-${prefix}-services'
        properties: {
          addressPrefix: servicesSubnetPrefix
          networkSecurityGroup: {
            id: servicesNsgId
          }
        }
      }
      {
        name: 'snet-function'
        properties: {
          addressPrefix: functionSubnetPrefix
          networkSecurityGroup: {
            id: functionNsgId
          }
          delegations: [
            {
              name: 'delegation-func'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output gatewaySubnetId string = vnet.properties.subnets[0].id
output servicesSubnetId string = vnet.properties.subnets[1].id
output functionSubnetId string = vnet.properties.subnets[2].id
