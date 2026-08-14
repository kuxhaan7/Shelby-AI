output "service_url" {
  description = "Cloud Run URL for this tenant."
  value       = google_cloud_run_v2_service.tenant.uri
}

output "data_bucket" {
  description = "GCS bucket mounted at /data for this tenant."
  value       = google_storage_bucket.data.name
}

output "service_account" {
  description = "Runtime service account email for this tenant."
  value       = google_service_account.tenant.email
}

output "secret_names" {
  description = "Secret Manager IDs for this tenant (namespaced)."
  value       = [for s in google_secret_manager_secret.tenant : s.secret_id]
}

output "custom_domain" {
  description = "Mapped custom domain (null if not set)."
  value       = length(google_cloud_run_domain_mapping.tenant) > 0 ? google_cloud_run_domain_mapping.tenant[0].name : null
}
