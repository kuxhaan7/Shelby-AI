variable "project_id" {
  description = "GCP project ID Shelby lives in."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Artifact Registry, and the data bucket."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name. Also used to derive resource names."
  type        = string
  default     = "shelby"
}

variable "image" {
  description = <<-EOT
    Container image Cloud Run will run.
    On the first apply, leave this as the default hello image so the service
    comes up green. Then build and push Shelby to Artifact Registry (see
    README) and rerun with the real tag, e.g.
    us-central1-docker.pkg.dev/<project>/shelby/shelby:latest
  EOT
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "shelby_model" {
  description = "Anthropic model id Shelby defaults to."
  type        = string
  default     = "claude-opus-4-7"
}

# ── Secrets ─────────────────────────────────────────────────────────────────
# Terraform CREATES the Secret Manager slot but does NOT put the value in
# state. You populate the value out-of-band with `gcloud secrets versions add`
# (see README). This keeps your API keys out of terraform.tfstate.

variable "managed_secrets" {
  description = "Names of secrets Cloud Run mounts as env vars. Values are set via gcloud, not Terraform."
  type        = set(string)
  default = [
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "TAVILY_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_NOTIFY_CHAT_ID",
    "KAGGLE_API_TOKEN",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_TRACING",
  ]
}
