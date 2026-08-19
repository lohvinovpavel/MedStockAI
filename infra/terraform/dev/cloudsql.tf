resource "random_password" "db" {
  length  = 24
  special = false # goes straight into a postgresql:// URL; skip URL-escaping headaches
}

# db-g1-small, zonal, HDD: shared-core tier with 1.7GB RAM.
# Backups stay on even in dev — this is a shared DB and someone will drop a
# table.
resource "google_sql_database_instance" "dev" {
  name             = "medstock-dev-pg"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_16"

  # Must be destroyable — see gke.tf for the same reasoning.
  deletion_protection = false

  settings {
    # New projects default new instances to Enterprise Plus edition, whose
    # tiers are all db-perf-optimized-*. db-g1-small is a shared-core
    # tier that only exists under Enterprise edition — must be pinned or the
    # API rejects the tier outright.
    edition           = "ENTERPRISE"
    tier              = "db-g1-small"
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    # activation_policy = "NEVER"

    # retained_backups is the knob for "keep 7 daily backups".
    # transaction_log_retention_days is NOT — it only governs point-in-time
    # recovery, which is off here because PITR bills for WAL storage.
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = false
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      ipv4_enabled    = true # public IP, so teammates can reach it via Cloud SQL Auth Proxy from their laptops
      private_network = google_compute_network.dev.id

      # IAM-authenticated Auth Proxy connections don't need an authorized-network
      # allowlist — the alternative is maintaining seven home IPs by hand.
    }
  }

  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.sql_peering,
  ]
}

resource "google_sql_database" "medstock" {
  name     = "medstock"
  project  = var.project_id
  instance = google_sql_database_instance.dev.name
}

resource "google_sql_user" "medstock" {
  name     = "medstock"
  project  = var.project_id
  instance = google_sql_database_instance.dev.name
  password = random_password.db.result

  depends_on = [
    google_sql_database.medstock
  ]
}
