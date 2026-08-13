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
  backend "gcs" {
    # bucket = "REPLACE-ME-tfstate-bucket"
    prefix = "medstock-dev"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
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
