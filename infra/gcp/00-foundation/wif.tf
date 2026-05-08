locals {
  github_repository_subjects = toset([
    for repository in var.github_repositories : "repo:${var.github_owner}/${repository}"
  ])
}

resource "google_service_account" "terraform_deployer" {
  project      = var.project_id
  account_id   = "bookflow-gcp-tf-deployer"
  display_name = "BOOKFLOW GCP Terraform deployer"
  description  = "Federated deployer used by GitHub Actions for GCP Terraform layers."

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
  ]
}

resource "google_project_iam_member" "terraform_deployer" {
  for_each = var.terraform_deployer_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.terraform_deployer.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "bookflow-github"
  display_name              = "BOOKFLOW GitHub Actions"
  description               = "OIDC trust pool for BOOKFLOW GitHub Actions workflows."
  disabled                  = false

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
  ]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions"
  description                        = "Accepts GitHub Actions OIDC tokens for configured BOOKFLOW repositories."
  disabled                           = false

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.aud"        = "assertion.aud"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = join(" || ", [
    for subject in local.github_repository_subjects : "assertion.repository == '${trimprefix(subject, "repo:")}'"
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_wif_deployer" {
  for_each = local.github_repository_subjects

  service_account_id = google_service_account.terraform_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${trimprefix(each.value, "repo:")}"
}
