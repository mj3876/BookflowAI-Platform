locals {
  cross_cloud_remote_cidrs = distinct(concat(var.aws_vpc_cidrs, [var.azure_vnet_cidr]))
}

resource "google_compute_firewall" "cross_cloud_ingress_private_api" {
  name        = "bookflow-allow-cross-cloud-private-api"
  project     = var.project_id
  network     = data.google_compute_network.bookflow_vpc.name
  description = "Allow least-privilege private cross-cloud ingress from AWS/Azure to tagged BOOKFLOW GCP services."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = local.cross_cloud_remote_cidrs
  target_tags   = var.private_service_target_tags

  allow {
    protocol = "tcp"
    ports    = var.cross_cloud_ingress_tcp_ports
  }
}

resource "google_compute_firewall" "cross_cloud_ingress_deny_all" {
  name        = "bookflow-deny-cross-cloud-ingress"
  project     = var.project_id
  network     = data.google_compute_network.bookflow_vpc.name
  description = "Deny all non-explicit private cross-cloud ingress to tagged BOOKFLOW GCP services."
  direction   = "INGRESS"
  priority    = 65534

  source_ranges = local.cross_cloud_remote_cidrs
  target_tags   = var.private_service_target_tags

  deny {
    protocol = "all"
  }
}

resource "google_compute_firewall" "cross_cloud_egress_private_api" {
  name        = "bookflow-allow-cross-cloud-egress"
  project     = var.project_id
  network     = data.google_compute_network.bookflow_vpc.name
  description = "Allow least-privilege private cross-cloud egress from tagged BOOKFLOW GCP services to AWS/Azure."
  direction   = "EGRESS"
  priority    = 1000

  destination_ranges = local.cross_cloud_remote_cidrs
  target_tags        = var.private_service_target_tags

  allow {
    protocol = "tcp"
    ports    = var.cross_cloud_egress_tcp_ports
  }
}

resource "google_compute_firewall" "cross_cloud_egress_deny_all" {
  name        = "bookflow-deny-cross-cloud-egress"
  project     = var.project_id
  network     = data.google_compute_network.bookflow_vpc.name
  description = "Deny all non-explicit private cross-cloud egress from tagged BOOKFLOW GCP services."
  direction   = "EGRESS"
  priority    = 65534

  destination_ranges = local.cross_cloud_remote_cidrs
  target_tags        = var.private_service_target_tags

  deny {
    protocol = "all"
  }
}
