resource "google_project_service" "vpcaccess" {
  project            = var.project_id
  service            = "vpcaccess.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_network" "bookflow_vpc" {
  name                    = "bookflow-vpc"
  project                 = var.project_id
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [
    google_project_service.required["compute.googleapis.com"],
  ]
}

resource "google_compute_subnetwork" "bookflow_main" {
  name          = "bookflow-main-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.bookflow_vpc.id
  ip_cidr_range = var.main_subnet_cidr
}

resource "google_vpc_access_connector" "bookflow" {
  name          = "bookflow-vpc-conn"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.bookflow_vpc.name
  ip_cidr_range = var.vpc_connector_cidr

  depends_on = [
    google_project_service.vpcaccess,
  ]
}

resource "google_compute_firewall" "bookflow_internal" {
  name        = "bookflow-allow-internal"
  project     = var.project_id
  network     = google_compute_network.bookflow_vpc.name
  description = "Allow internal BOOKFLOW traffic inside the GCP foundation VPC."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges = concat([var.vpc_cidr], var.aws_allowed_cidrs)

  allow {
    protocol = "all"
  }
}

# 1.     IP  
resource "google_compute_global_address" "private_ip_alloc" {
  name          = "google-managed-services-bookflow-vpc"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.bookflow_vpc.id
  project       = var.project_id
}

# 2.   (VPC ) 
resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.bookflow_vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]

  #   API    
  depends_on = [
    google_project_service.required["servicenetworking.googleapis.com"]
  ]
}
