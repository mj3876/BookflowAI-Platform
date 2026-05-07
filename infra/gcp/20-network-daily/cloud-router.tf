variable "gcp_router_asn" {
  description = "Private ASN for the GCP Cloud Router."
  type        = number
}

resource "google_compute_router" "bookflow_aws_router" {
  name    = "bookflow-aws-cr"
  project = var.project_id
  region  = var.region
  network = data.google_compute_network.bookflow_vpc.id

  bgp {
    asn            = var.gcp_router_asn
    advertise_mode = "CUSTOM"

    advertised_groups = [
      "ALL_SUBNETS",
    ]

    advertised_ip_ranges {
      range       = var.gcp_routed_cidr
      description = "PSC endpoint range routed from AWS TGW to GCP private Google APIs."
    }
  }
}
