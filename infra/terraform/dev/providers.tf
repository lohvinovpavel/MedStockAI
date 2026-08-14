terraform {
  required_version = ">= 1.9"

  required_providers {
    google     = { source = "hashicorp/google", version = "~> 6.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.0" }
    helm       = { source = "hashicorp/helm", version = "~> 2.17" }
    tls        = { source = "hashicorp/tls" }
    random     = { source = "hashicorp/random" }
  }

  # Bucket created by hand before the first `init` (see infra/README.md) —
  # a bootstrap module for one bucket is more Terraform than the problem
  # deserves. Fill in with -backend-config or a bucket= line here.
  #
  # impersonate_service_account here is the backend's own setting, separate
  # from the `provider "google"` block below — the gcs backend authenticates
  # state access independently of the resource providers, so it needs the
  # same impersonation repeated, not inherited. Without it, CI's
  # github-actions-infra (whose only grant is token-creation on
  # terraform-iac, by design — see ci.tf) has no bucket access of its own
  # and `terraform init` 403s on storage.objects.list. A human's local
  # apply works without this today only because their own account happens
  # to have direct bucket access too; harmless either way. Literal value,
  # not var.project_id — backend blocks can't reference variables.
  backend "gcs" {
    # bucket = "REPLACE-ME-tfstate-bucket"
    bucket                      = "fde_terraform_state"
    prefix                      = "medstock-dev"
    impersonate_service_account = "terraform-iac@project-f1b68703-0a5c-4fde-88a.iam.gserviceaccount.com"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone

  impersonate_service_account = "terraform-iac@project-f1b68703-0a5c-4fde-88a.iam.gserviceaccount.com"
}

# GKE cluster doesn't exist at plan time on a fresh apply, so kubernetes/helm
# providers can't read a kubeconfig file — they authenticate the same way the
# google provider already did, against the cluster this same apply creates.
data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.dev.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.dev.master_auth[0].cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = "https://${google_container_cluster.dev.endpoint}"
    token                  = data.google_client_config.default.access_token
    cluster_ca_certificate = base64decode(google_container_cluster.dev.master_auth[0].cluster_ca_certificate)
  }
}
