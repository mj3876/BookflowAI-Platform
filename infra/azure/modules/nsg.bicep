// modules/nsg.bicep
// Services / Function 서브넷용 NSG

param location string
param prefix string

resource servicesNsg 'Microsoft.Network/networkSecurityGroups@2023-05-01' = {
  name: 'nsg-${prefix}-services'
  location: location
  properties: {
    securityRules: [
      {
        name: 'deny-internet-inbound'
        properties: {
          priority: 4000
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'Internet'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource functionNsg 'Microsoft.Network/networkSecurityGroups@2023-05-01' = {
  name: 'nsg-${prefix}-function'
  location: location
  properties: {
    securityRules: [
      {
        name: 'allow-https-inbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefix: 'AzureLoadBalancer'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'deny-internet-inbound'
        properties: {
          priority: 4000
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'Internet'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

output servicesNsgId string = servicesNsg.id
output functionNsgId string = functionNsg.id
