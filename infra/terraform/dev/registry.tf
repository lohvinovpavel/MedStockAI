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

  # Tags are commit SHAs (§7 of the README) — every push is a new tag, so the
  # untagged policy above never touches them. AR's actual "keep last N" shape
  # is a DELETE policy matching all tagged versions, with a KEEP policy as
  # the exemption — KEEP alone deletes nothing, it just protects the 3 most
  # recently uploaded versions of each image from the DELETE policy below.
  cleanup_policies {
    id     = "keep-last-3-tagged"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }

  cleanup_policies {
    id     = "delete-old-tagged"
    action = "DELETE"
    condition {
      tag_state = "TAGGED"
    }
  }

  depends_on = [google_project_service.apis]
}
