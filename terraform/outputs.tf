output "service_url" {
  description = "The URL Cloud Run assigned to Shelby. Open this in a browser."
  value       = google_cloud_run_v2_service.shelby.uri
}

output "image_repo" {
  description = "Push Shelby's container here (docker push <this>/shelby:latest)."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.shelby.repository_id}"
}

output "data_bucket" {
  description = "GCS bucket mounted at /data inside the container."
  value       = google_storage_bucket.data.name
}

output "tfstate_bucket" {
  description = "GCS bucket for remote Terraform state (paste into backend.tf)."
  value       = google_storage_bucket.tfstate.name
}

output "service_account" {
  description = "Shelby's runtime service account email."
  value       = google_service_account.shelby.email
}

output "secret_names" {
  description = "Secret Manager slots created for Shelby. Populate with `gcloud secrets versions add`."
  value       = [for s in google_secret_manager_secret.shelby : s.secret_id]
}
