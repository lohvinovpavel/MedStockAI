# Lets GitHub Actions push images and deploy without a downloadable service
# account key: GitHub's own OIDC token gets exchanged for a short-lived GCP
# credential, scoped to this one repo. See infra/README.md §11.
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  # Restrict which repo can mint tokens at all — belt, not just the
  # per-SA binding below (suspenders).
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

// Two SAs off the one WIF pool above, not one shared SA — deploy-dev.yml
// (docker push, kubectl apply) and infra-ci/infra-cd.yml (terraform
// plan/apply) have very different blast radii if a workflow or a dependency
// in it ever goes bad. Same trust boundary (the pool already restricts to
// var.github_repo), different permission boundary per SA.

# --- deploy: builds/pushes images, applies k8s manifests -------------------

resource "google_service_account" "github_actions_deploy" {
  project      = var.project_id
  account_id   = "github-actions-deploy"
  display_name = "GitHub Actions CI (dev deploy)"
}

resource "google_service_account_iam_member" "github_actions_deploy_wif" {
  service_account_id = google_service_account.github_actions_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_project_iam_member" "github_actions_deploy_roles" {
  for_each = toset(["roles/artifactregistry.writer", "roles/container.developer"])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_actions_deploy.email}"
}

# --- infra: terraform plan/apply --------------------------------------------

resource "google_service_account" "github_actions_infra" {
  project      = var.project_id
  account_id   = "github-actions-infra"
  display_name = "GitHub Actions CI (terraform plan/apply)"
}

resource "google_service_account_iam_member" "github_actions_infra_wif" {
  service_account_id = google_service_account.github_actions_infra.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# infra-ci/infra-cd run `terraform plan`/`apply` as this SA, and providers.tf
# always impersonates terraform-iac (same as a human's local apply) — so
# this SA needs token-creation rights on that identity; it carries no
# project-level roles of its own, everything flows through the
# impersonation. terraform-iac itself isn't a resource here (bootstrapped
# manually, like the state bucket); this grant is chicken-and-egg the same
# way — a human applies it once locally before the first infra-cd run can
# authenticate.
resource "google_service_account_iam_member" "github_actions_infra_impersonate_terraform_iac" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/terraform-iac@${var.project_id}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.github_actions_infra.email}"
}
