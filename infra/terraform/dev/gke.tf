# Zonal Standard cluster: the GKE free tier credits $74.40/mo of cluster
# management fee, enough for exactly one zonal cluster, so the control plane
# is effectively $0. A regional cluster carries the same fee but triples the
# node count.
resource "google_container_cluster" "dev" {
  name     = "medstock-dev"
  location = var.zone
  project  = var.project_id

  network    = google_compute_network.dev.id
  subnetwork = google_compute_subnetwork.dev.id

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Managed separately below as a Spot pool — the default pool would be
  # on-demand e2-medium and we don't want both.
  remove_default_node_pool = true
  initial_node_count       = 1

  release_channel {
    channel = "REGULAR"
  }

  # This must be destroyable — it's a sandbox, not production.
  deletion_protection = false

  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.sql_peering,
  ]
}

# Nodes pull images as the default Compute Engine service account (node_config
# has no service_account override below). cloud-platform oauth_scopes only
# sets what scope a node's token CAN carry — it still needs the IAM role to
# actually read Artifact Registry, or every pull 403s.
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_iam_member" "gke_nodes_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

# Spot: ~70% off on-demand, and preemption here just costs a pod restart —
# a trade production can't make but a sandbox can.
resource "google_container_node_pool" "spot" {
  name     = "spot-pool"
  location = var.zone
  cluster  = google_container_cluster.dev.name
  project  = var.project_id

  autoscaling {
    min_node_count = 1
    max_node_count = 1
  }
  initial_node_count = 1

  node_config {
    machine_type = "e2-medium"
    spot         = true

    disk_type    = "pd-balanced"
    disk_size_gb = 20

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
