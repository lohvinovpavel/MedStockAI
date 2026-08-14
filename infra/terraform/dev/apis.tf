# Every resource below that calls one of these APIs `depends_on` it —
# enabling and using an API in the same apply is a documented race otherwise.
locals {
  apis = [
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "iamcredentials.googleapis.com", # token exchange for Workload Identity Federation (ci.tf)
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  project = var.project_id
  service = each.value

  # This is a shared sandbox someone will eventually `terraform destroy`;
  # disabling the API out from under the project on destroy would break
  # anything else running there.
  disable_on_destroy = false
}
