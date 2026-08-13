# ── APIs ────────────────────────────────────────────────────────────────────
# Every GCP service Shelby touches has to be explicitly enabled on the project
# before Terraform can create resources against it. Do this once, up-front, so
# later resource blocks aren't racing an unenabled API.

locals {
  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each                   = toset(local.required_apis)
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# ── Terraform state bucket ──────────────────────────────────────────────────
# Created on the first apply so backend.tf can point at it on the second
# init. Versioning on so a bad apply doesn't erase history irreversibly.

resource "google_storage_bucket" "tfstate" {
  name                        = "shelby-tfstate-${var.project_id}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}

# ── Data bucket (SHELBY_DATA_DIR) ───────────────────────────────────────────
# Cloud Run mounts this as a filesystem volume via gcsfuse so ChromaDB, learned
# skills, scheduled tasks, and the usage log all survive redeploys — same role
# the Railway volume plays today.

resource "google_storage_bucket" "data" {
  name                        = "${var.service_name}-data-${var.project_id}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  depends_on = [google_project_service.enabled]
}

# ── Artifact Registry (container images) ────────────────────────────────────
resource "google_artifact_registry_repository" "shelby" {
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  description   = "Shelby container images"

  depends_on = [google_project_service.enabled]
}

# ── Service account for the Cloud Run service ───────────────────────────────
# Least-privilege: only what Shelby needs to read its secrets and read/write
# its data bucket. No broad project-level roles.

resource "google_service_account" "shelby" {
  account_id   = "${var.service_name}-runtime"
  display_name = "Shelby Cloud Run runtime"
}

resource "google_storage_bucket_iam_member" "data_access" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.shelby.email}"
}

# ── Secret Manager slots ────────────────────────────────────────────────────
# Terraform creates the SLOT for each secret so Cloud Run can reference it.
# The VALUE is set out-of-band with `gcloud secrets versions add` so it never
# lands in Terraform state. See README section "Populating secrets".

resource "google_secret_manager_secret" "shelby" {
  for_each  = var.managed_secrets
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "shelby_access" {
  for_each  = var.managed_secrets
  secret_id = google_secret_manager_secret.shelby[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.shelby.email}"
}

# ── Cloud Run service ───────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "shelby" {
  name     = var.service_name
  location = var.region

  template {
    service_account = google_service_account.shelby.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "SHELBY_MODEL"
        value = var.shelby_model
      }

      env {
        name  = "SHELBY_DATA_DIR"
        value = "/data"
      }

      env {
        name  = "CHROMA_PERSIST_DIR"
        value = "/data/chroma"
      }

      dynamic "env" {
        for_each = var.managed_secrets
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.shelby[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "data"
        mount_path = "/data"
      }
    }

    volumes {
      name = "data"
      gcs {
        bucket    = google_storage_bucket.data.name
        read_only = false
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.shelby_access,
    google_storage_bucket_iam_member.data_access,
  ]
}

# Make the service reachable without auth so the web UI works from a browser.
# For a customer deployment you'd remove this and put an identity in front.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.shelby.name
  location = google_cloud_run_v2_service.shelby.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
