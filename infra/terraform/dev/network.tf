resource "google_compute_network" "dev" {
  name                    = "medstock-dev"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "dev" {
  name          = "medstock-dev-subnet"
  region        = var.region
  network       = google_compute_network.dev.id
  ip_cidr_range = "10.10.0.0/20"

  # VPC-native GKE reads pod/service ranges by name, not CIDR — the cluster
  # resource just points at these.
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# Cloud SQL private IP needs its own /24 out of the peering range, VPC peered
# to the Google-managed services network — this is that reservation.
resource "google_compute_global_address" "sql_peering" {
  name          = "medstock-dev-sql-peering"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 24
  network       = google_compute_network.dev.id

  depends_on = [google_project_service.apis]
}

resource "google_service_networking_connection" "sql_peering" {
  network                 = google_compute_network.dev.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_peering.name]

  depends_on = [google_project_service.apis]
}

# Pinned so the sslip.io hostname (derived from this IP) never moves across
# `helm upgrade`s of ingress-nginx.
resource "google_compute_address" "ingress" {
  name         = "medstock-dev-ingress"
  region       = var.region
  address_type = "EXTERNAL"

  depends_on = [google_project_service.apis]
}
