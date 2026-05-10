resource "google_vpc_access_connector" "bookflow" {
  name          = local.vpc_connector_name
  project       = var.project_id
  region        = local.region
  network       = data.google_compute_network.bookflow_vpc.name
  ip_cidr_range = var.vpc_connector_cidr

  depends_on = [
    google_project_service.required["vpcaccess.googleapis.com"],
  ]
}
