variable "aws_vpc_cidrs" {
  description = "AWS VPC CIDR ranges used for cross-cloud routing inputs."
  type        = list(string)
}

variable "azure_vnet_cidr" {
  description = "Azure VNet CIDR range used for cross-cloud firewall policy alignment."
  type        = string
}

variable "aws_peer_ips" {
  description = "AWS VPN public peer IPs exposed for the two TGW tunnel endpoints."
  type        = list(string)

  validation {
    condition     = length(var.aws_peer_ips) == 2
    error_message = "aws_peer_ips must contain exactly two AWS TGW VPN outside public IPs for TWO_IPS_REDUNDANCY."
  }
}

variable "aws_tgw_bgp_asn" {
  description = "Private ASN used by the AWS Transit Gateway / VPN attachment."
  type        = number
}

variable "vpn_shared_secret" {
  description = "Pre-shared key reused by the HA VPN tunnels. Must match the AWS GcpPresharedKey parameter."
  type        = string
  sensitive   = true
}

variable "bgp_sessions" {
  description = "Per-tunnel BGP settings for the two HA VPN tunnels."
  type = map(object({
    vpn_gateway_interface           = number
    peer_external_gateway_interface = number
    router_ip_cidr                  = string
    peer_ip_address                 = string
    advertised_route_priority       = optional(number, 100)
  }))

  validation {
    condition = (
      length(var.bgp_sessions) == 2 &&
      alltrue([
        for session in values(var.bgp_sessions) :
        contains([0, 1], session.vpn_gateway_interface) &&
        contains([0, 1], session.peer_external_gateway_interface)
      ])
    )
    error_message = "bgp_sessions must define exactly two tunnels using GCP HA VPN interfaces 0/1 and AWS external gateway interfaces 0/1."
  }
}

variable "gcp_routed_cidr" {
  description = "GCP CIDR that AWS routes toward the TGW/VPN path. Must contain psc_endpoint_ip for AWS-to-PSC access."
  type        = string
}

variable "psc_endpoint_host_offset" {
  description = "Host offset inside gcp_routed_cidr used for the Google APIs Private Service Connect endpoint."
  type        = number
}

variable "private_service_target_tags" {
  description = "Network tags for GCP workloads allowed to receive translated cross-cloud private traffic."
  type        = list(string)
  default     = ["bookflow-private-api"]
}

variable "cross_cloud_ingress_tcp_ports" {
  description = "TCP ports allowed from AWS VPCs and Azure VNet into tagged GCP private workloads."
  type        = list(string)
  default     = ["443"]
}

variable "cross_cloud_egress_tcp_ports" {
  description = "TCP ports allowed from tagged GCP private workloads toward AWS VPCs and Azure VNet."
  type        = list(string)
  default     = ["443"]
}
