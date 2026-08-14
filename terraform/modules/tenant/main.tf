locals {
  # Every resource this module creates is prefixed with the tenant_id, so a
  # look at the GCP console makes it obvious which tenant owns what. Isolation
  # at the naming layer is a cheap defense-in-depth on top of the IAM boundaries.
  name_prefix = "shelby-${var.tenant_id}"
}

# ── Runtime service account (per tenant) ────────────────────────────────────
resource "google_service_account" "tenant" {
  account_id   = "${local.name_prefix}-runtime"
  display_name = "Shelby runtime — tenant ${var.tenant_id}"
}

# ── Data bucket (per tenant) ────────────────────────────────────────────────
# Mounted at /data inside the container via gcsfuse. Chroma, learned skills,
# scheduled tasks, and the usage log for this tenant all live here.
resource "google_storage_bucket" "data" {
  name                        = "shelby-data-${var.tenant_id}-${var.project_id}"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
}

# Bucket-scoped IAM. Tenant A's SA can only touch tenant A's bucket. Cross-tenant
# reads are impossible without another explicit grant — the exact isolation
# story an enterprise customer wants to hear.
resource "google_storage_bucket_iam_member" "data_access" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.tenant.email}"
}

# ── Secret Manager (per tenant) ─────────────────────────────────────────────
# Secret IDs are namespaced: acme_ANTHROPIC_API_KEY, initech_ANTHROPIC_API_KEY.
# A tenant's runtime SA only has secretAccessor on its own namespaced set, so
# there's no way for tenant A's container to pull tenant B's Anthropic key
# even if it tries the exact secret name.
resource "google_secret_manager_secret" "tenant" {
  for_each  = var.managed_secrets
  secret_id = "${var.tenant_id}_${each.value}"

  replication {
    auto {}
  }

  labels = {
    tenant = var.tenant_id
  }
}

# Placeholder version so Cloud Run's `latest` reference resolves on the first
# apply. Real values overwrite via `gcloud secrets versions add` (or the
# migrate_secrets.sh script if you're moving from a single-tenant deploy).
resource "google_secret_manager_secret_version" "placeholder" {
  for_each    = var.managed_secrets
  secret      = google_secret_manager_secret.tenant[each.value].id
  secret_data = "REPLACE_ME"

  lifecycle {
    ignore_changes = [secret_data]
  }
}

resource "google_secret_manager_secret_iam_member" "tenant_access" {
  for_each  = var.managed_secrets
  secret_id = google_secret_manager_secret.tenant[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.tenant.email}"
}

# ── Cloud Run service (per tenant) ──────────────────────────────────────────
resource "google_cloud_run_v2_service" "tenant" {
  name     = local.name_prefix
  location = var.region

  template {
    service_account = google_service_account.tenant.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    labels = {
      tenant = var.tenant_id
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

      # Tells Shelby which tenant it's serving. Read at startup and included
      # in structured logs / audit records; groundwork for Phase 4's per-tenant
      # data-access policies.
      env {
        name  = "SHELBY_TENANT_ID"
        value = var.tenant_id
      }

      dynamic "env" {
        for_each = var.google_site_verification != "" ? [var.google_site_verification] : []
        content {
          name  = "GOOGLE_SITE_VERIFICATION"
          value = env.value
        }
      }

      # Namespaced secrets → unprefixed env var names inside the container.
      # Shelby still reads ANTHROPIC_API_KEY; only the Secret Manager slot
      # underneath is per-tenant.
      dynamic "env" {
        for_each = var.managed_secrets
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.tenant[env.value].secret_id
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
    google_secret_manager_secret_iam_member.tenant_access,
    google_secret_manager_secret_version.placeholder,
    google_storage_bucket_iam_member.data_access,
  ]
}

# Public invoker so the web UI is reachable in a browser. Enterprise deploys
# would remove this and put an identity-aware proxy in front per tenant.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.tenant.name
  location = google_cloud_run_v2_service.tenant.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Optional custom domain (per tenant) ─────────────────────────────────────
resource "google_cloud_run_domain_mapping" "tenant" {
  count = var.custom_domain != "" ? 1 : 0

  name     = var.custom_domain
  location = var.region

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.tenant.name
  }
}
