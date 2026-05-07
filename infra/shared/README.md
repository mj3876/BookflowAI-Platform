# Shared Infrastructure Contract

This directory documents the cross-cloud network values that must stay aligned
between the AWS, Azure, and GCP infrastructure layers.

The files under `infra/aws` and `infra/azure` are treated as read-only
references for this contract. Do not store real secrets, pre-shared keys,
private certificates, service account keys, or credential JSON in this
directory.

## Files

```text
network.example.tfvars.json
```

`network.example.tfvars.json` is a template and coordination record. It is not a
direct Terraform input file. Copy the relevant values into each cloud-specific
parameter file only after replacing placeholders with environment-specific
values.

## Source References

Observed references in this repository:

| Area | Reference file | Contract value |
|---|---|---|
| AWS TGW ASN | `infra/aws/60-network-cross-cloud/tgw.yaml` | `64512` |
| AWS VPC CIDRs | `infra/aws/10-network-core/vpc-*.yaml` | `10.0.0.0/16` through `10.4.0.0/16` |
| AWS to GCP VPN tunnel CIDRs | `infra/aws/60-network-cross-cloud/vpn-site-to-site.yaml` | `169.254.21.0/30`, `169.254.22.0/30` |
| Azure VNet and BGP ASN | `infra/azure/parameters/dev.json.example` | `172.16.0.0/16`, ASN `65001` |
| GCP network variables | `infra/gcp/20-network-daily/variables.tf` | `aws_peer_ips`, `bgp_sessions`, `gcp_routed_cidr`, PSC host offset |

## AWS CIDR Scope

`aws.vpc_cidrs` contains every AWS VPC CIDR known to the platform.

`aws.routed_vpc_cidrs_to_gcp` is the subset currently passed to
`infra/gcp/20-network-daily/terraform.tfvars` as `aws_vpc_cidrs`. It excludes
the Ansible VPC because the current GCP daily network layer only routes the
application, sales, egress, and data VPCs.

## GCP Mapping

When preparing `infra/gcp/20-network-daily/terraform.tfvars`, use:

| Shared key | GCP variable |
|---|---|
| `aws.tgw_bgp_asn` | `aws_tgw_bgp_asn` |
| `aws.routed_vpc_cidrs_to_gcp` | `aws_vpc_cidrs` |
| `aws.vpn.gcp.tunnels[*].aws_outside_ip` | `aws_peer_ips` |
| `aws.vpn.gcp.tunnels[*].gcp_router_ip_cidr` | `bgp_sessions[*].router_ip_cidr` |
| `aws.vpn.gcp.tunnels[*].aws_bgp_peer_ip` | `bgp_sessions[*].peer_ip_address` |
| `gcp.router_bgp_asn` | `gcp_router_asn` |
| `gcp.routed_cidr` | `gcp_routed_cidr` |
| `gcp.psc_endpoint_host_offset` | `psc_endpoint_host_offset` |

The GCP tunnel mapping assumes AWS uses the `.1` address inside each `/30`
tunnel CIDR and GCP Cloud Router uses the `.2/30` address.

## Azure Mapping

When deploying `infra/azure/main.bicep`, align:

| Shared key | Azure parameter |
|---|---|
| `azure.location` | `location` |
| `azure.vnet_cidr` | `vnetAddressPrefix` |
| `azure.subnets.gateway` | `gatewaySubnetPrefix` |
| `azure.subnets.services` | `servicesSubnetPrefix` |
| `azure.subnets.function` | `functionSubnetPrefix` |
| `azure.vpn.bgp_asn` | `vpnBgpAsn` |

The Azure VPN connection module currently models one AWS active peer. If a
second standby connection is enabled later, add the second AWS tunnel outside IP
and matching BGP peering address to the environment-specific Azure deployment
parameters.

## AWS Mapping

When deploying the AWS cross-cloud layer:

| Shared key | AWS parameter |
|---|---|
| `aws.tgw_bgp_asn` | `TgwAsn` in `tgw.yaml` |
| `azure.vpn.public_ips.active` | `AzureVpnGatewayIp` in `customer-gateway.yaml` |
| `azure.vpn.bgp_asn` | `AzureBgpAsn` in `customer-gateway.yaml` |
| `gcp.vpn.aws_customer_gateway_ip` | `GcpHaVpnIp` in `customer-gateway.yaml` |
| `gcp.router_bgp_asn` | `GcpBgpAsn` in `customer-gateway.yaml` |

`network.example.tfvars.json` intentionally uses placeholders for public VPN
peer IPs because those values are assigned by the cloud providers at deployment
time.

The current AWS Customer Gateway template accepts one GCP peer IP. Keep
`gcp.vpn.public_ips` as the full GCP HA VPN interface inventory and set
`gcp.vpn.aws_customer_gateway_ip` to the specific GCP interface IP selected for
the AWS `GcpHaVpnIp` parameter.

## Security Rules

Do not commit:

- VPN pre-shared keys
- AWS access keys
- Azure client secrets
- GCP service account keys
- Real credential JSON files
- Private certificates or PEM files
