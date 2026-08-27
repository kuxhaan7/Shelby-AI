variable "project_id" {
  description = "GCP project ID Shelby lives in."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Artifact Registry, and every tenant's data bucket."
  type        = string
  default     = "us-central1"
}

# ── Tenants ─────────────────────────────────────────────────────────────────
# The list of tenant ids that get a Shelby stack. Add an entry, apply, and a
# fully isolated deploy (Cloud Run service, GCS bucket, namespaced secrets,
# runtime SA) appears. Drop an entry to tear one down.
#
# Defaults to a single tenant named "default" so a fresh apply behaves like
# the old single-tenant setup — just under a namespaced resource name.
variable "tenants" {
  description = "List of tenant ids. One Shelby stack per entry."
  type        = list(string)
  default     = ["default"]

  validation {
    condition     = length(var.tenants) > 0
    error_message = "At least one tenant is required."
  }
}

# ── Shared image & model config ─────────────────────────────────────────────
# Every tenant runs the same container image and defaults to the same model.
# You can pin per-tenant models later by widening this into a map if a
# customer wants Opus while another wants Haiku.

variable "image" {
  description = <<-EOT
    Container image every tenant's Cloud Run service runs. On the first apply
    leave this as the default hello image so services come up green. Then
    build and push Shelby to Artifact Registry and rerun with the real tag.
  EOT
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "shelby_model" {
  description = "Anthropic model id Shelby defaults to (all tenants)."
  type        = string
  default     = "claude-opus-4-7"
}

# ── Secrets contract ────────────────────────────────────────────────────────
# Names Cloud Run mounts as env vars inside every tenant's container. Actual
# Secret Manager IDs are namespaced per tenant (acme_ANTHROPIC_API_KEY etc.)
# so a tenant can never read another tenant's key.

variable "managed_secrets" {
  description = "Env var names Cloud Run mounts from Secret Manager, per tenant. Actual secret IDs are namespaced with the tenant id."
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
    "VOYAGE_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
  ]
}

# ── Per-tenant optional overrides ───────────────────────────────────────────
# Maps keyed by tenant_id. Only tenants named here get a custom domain / a
# verification code; everyone else falls back to the raw Cloud Run URL and
# no meta tag.

variable "custom_domains" {
  description = <<-EOT
    Custom domain per tenant, e.g. { acme = "acme.shelby.is-a.dev" }. A tenant
    with no entry doesn't get a domain mapping. Each domain still needs its
    CNAME record and Search Console verification before it works.
  EOT
  type        = map(string)
  default     = {}
}

variable "google_site_verifications" {
  description = "Google Search Console verification codes per tenant, keyed by tenant id."
  type        = map(string)
  default     = {}
}
