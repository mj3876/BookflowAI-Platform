# WARNING:
# This PSC layer is a high-cost daily resource set for BOOKFLOW.
# Per the architecture rule, deploy only during 09:00-18:00 KST via start-day.sh
# and destroy after business hours via stop-day.sh.

locals {
  psc_endpoint_ip = cidrhost(var.gcp_routed_cidr, var.psc_endpoint_host_offset)
}

resource "google_project_service" "dns" {
  project            = var.project_id
  service            = "dns.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_global_address" "psc_googleapis_ip" {
  name         = "bookflow-psc-googleapis-ip"
  project      = var.project_id
  address_type = "INTERNAL"
  purpose      = "PRIVATE_SERVICE_CONNECT"
  network      = data.google_compute_network.bookflow_vpc.id
  address      = local.psc_endpoint_ip
}

resource "google_compute_global_forwarding_rule" "psc_googleapis" {
  name                  = "bookflowpscapi"
  project               = var.project_id
  network               = data.google_compute_network.bookflow_vpc.id
  ip_address            = google_compute_global_address.psc_googleapis_ip.id
  target                = "all-apis"
  load_balancing_scheme = ""

  depends_on = [
    google_compute_global_address.psc_googleapis_ip,
  ]
}

resource "google_dns_managed_zone" "googleapis_private" {
  name        = "bookflow-googleapis-private"
  project     = var.project_id
  dns_name    = "googleapis.com."
  description = "Private DNS zone for PSC-backed Google APIs access."

  visibility = "private"

  private_visibility_config {
    networks {
      network_url = data.google_compute_network.bookflow_vpc.id
    }
  }

  depends_on = [
    google_project_service.dns,
  ]
}

resource "google_dns_record_set" "private_googleapis" {
  name         = "private.googleapis.com."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis_private.name
  type         = "A"
  ttl          = 300
  rrdatas      = [local.psc_endpoint_ip]
}

resource "google_dns_record_set" "wildcard_private_googleapis" {
  name         = "*.private.googleapis.com."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis_private.name
  type         = "A"
  ttl          = 300
  rrdatas      = [local.psc_endpoint_ip]
}

resource "google_dns_record_set" "bigquery_googleapis" {
  name         = "bigquery.googleapis.com."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis_private.name
  type         = "A"
  ttl          = 300
  rrdatas      = [local.psc_endpoint_ip]
}
