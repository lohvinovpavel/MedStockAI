output "cluster_name" {
  value = google_container_cluster.dev.name
}

output "get_credentials_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.dev.name} --zone ${var.zone} --project ${var.project_id}"
}

output "ingress_ip" {
  value = google_compute_address.ingress.address
}

# sslip.io resolves any dash-separated IP embedded in the hostname back to
# that IP — no domain to buy, no DNS to manage. The Secure-cookie constraint
# (docs/infra-dev-plan.md §0.2) is why this needs to be a real hostname at
# all, not a bare IP.
output "ingress_host" {
  value = "medstock-dev.${replace(google_compute_address.ingress.address, ".", "-")}.sslip.io"
}

output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.medstock.repository_id}"
}

output "cloudsql_connection_name" {
  value = google_sql_database_instance.dev.connection_name
}

output "cloudsql_public_ip" {
  value = google_sql_database_instance.dev.public_ip_address
}

output "letsencrypt_email" {
  value = var.letsencrypt_email
}

# Not secret — these are what the GitHub Actions workflows' WIF config
# needs, and knowing them grants nothing without a run from github_repo
# itself. One provider, two SAs — see ci.tf.
output "ci_workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "ci_deploy_service_account_email" {
  value = google_service_account.github_actions_deploy.email
}

output "ci_infra_service_account_email" {
  value = google_service_account.github_actions_infra.email
}
