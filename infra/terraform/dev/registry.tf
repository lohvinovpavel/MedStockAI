resource "google_artifact_registry_repository" "medstock" {
  project       = var.project_id
  location      = var.region
  repository_id = "medstock"
  format        = "DOCKER"

  # Untagged images older than 7 days get swept — keeps storage inside the
  # 0.5GB free allowance instead of accumulating every `:latest` rebuild.
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s" # 7d
    }
  }

  depends_on = [google_project_service.apis]
}
