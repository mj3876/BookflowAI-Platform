variable "project_id" {
  description = "GCP project ID for BOOKFLOW."
  type        = string
}

variable "region" {
  description = "Primary GCP region."
  type        = string
}

variable "aws_tgw_bgp_asn" {
  description = "Private ASN used by the AWS Transit Gateway / VPN attachment."
  type        = number
}

variable "aws_vpc_cidrs" {
  description = "Mock or real AWS VPC CIDR ranges used for cross-cloud routing."
  type        = list(string)
}

variable "vpn_shared_secrets" {
  description = "Pre-shared keys for the two HA VPN tunnels."
  type        = map(string)
  sensitive   = true
}

variable "bgp_sessions" {
  description = "Per-tunnel BGP session settings for the two HA VPN tunnels."
  type        = any
}
