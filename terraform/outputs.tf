output "tenants" {
  description = "Map of tenant_id → per-tenant outputs (service URL, bucket, SA, secret names, custom domain)."
  value = {
    for id, m in module.tenant : id => {
      service_url     = m.service_url
      data_bucket     = m.data_bucket
      service_account = m.service_account
      secret_names    = m.secret_names
      custom_domain   = m.custom_domain
    }
  }
}

output "image_repo" {
  description = "Push Shelby's container here (docker push <this>/shelby:latest). One repo shared across every tenant."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.shelby.repository_id}"
}

output "tfstate_bucket" {
  description = "GCS bucket for remote Terraform state (paste into backend.tf)."
  value       = google_storage_bucket.tfstate.name
}
