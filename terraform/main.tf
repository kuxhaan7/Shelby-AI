# ── Shared: APIs ────────────────────────────────────────────────────────────
# Every GCP service any tenant might touch is enabled once at the project
# level. Cheaper and cleaner than re-enabling per tenant.

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

# ── Shared: Terraform state bucket ──────────────────────────────────────────
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

# ── Shared: Artifact Registry ───────────────────────────────────────────────
# One Docker repo, one Shelby image, all tenants pull from it. Tenants share
# code, isolate data.
resource "google_artifact_registry_repository" "shelby" {
  location      = var.region
  repository_id = "shelby"
  format        = "DOCKER"
  description   = "Shelby container images (shared across tenants)"

  depends_on = [google_project_service.enabled]
}

# ── Per-tenant stack ────────────────────────────────────────────────────────
# One module call per tenant. Each call materializes a full isolated Shelby:
# runtime SA, data bucket, namespaced Secret Manager slots, Cloud Run service,
# and (optionally) a custom domain mapping.
#
# Adding a tenant: append the id to var.tenants and terraform apply. Removing:
# drop the id and apply — Terraform destroys just that tenant's resources.

module "tenant" {
  for_each = toset(var.tenants)
  source   = "./modules/tenant"

  tenant_id       = each.key
  project_id      = var.project_id
  region          = var.region
  image           = var.image
  shelby_model    = var.shelby_model
  managed_secrets = var.managed_secrets

  # Per-tenant maps default to empty — a tenant only gets a custom domain or
  # verification code if you set one explicitly.
  custom_domain            = try(var.custom_domains[each.key], "")
  google_site_verification = try(var.google_site_verifications[each.key], "")

  depends_on = [
    google_project_service.enabled,
    google_artifact_registry_repository.shelby,
  ]
}
