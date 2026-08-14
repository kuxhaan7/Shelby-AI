variable "tenant_id" {
  description = "Short identifier for the tenant (lowercase, no spaces). Used to derive every resource name so isolation is enforced at the naming layer, not just IAM."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,29}$", var.tenant_id))
    error_message = "tenant_id must start with a lowercase letter and contain only lowercase letters, digits, and hyphens (max 30 chars)."
  }
}

variable "project_id" {
  description = "GCP project ID everything lives in."
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud Run service, data bucket, and cert."
  type        = string
}

variable "image" {
  description = "Container image the tenant's Cloud Run service runs."
  type        = string
}

variable "shelby_model" {
  description = "Anthropic model id Shelby defaults to for this tenant."
  type        = string
}

variable "managed_secrets" {
  description = "Env var names Cloud Run pulls from Secret Manager. Actual secret ids are namespaced with the tenant_id (e.g. acme_ANTHROPIC_API_KEY) so tenants can't read each other's keys."
  type        = set(string)
}

variable "custom_domain" {
  description = "Optional custom domain (e.g. acme.shelby.is-a.dev) mapped to this tenant. Empty = no mapping. Requires DNS + Search Console verification before it applies."
  type        = string
  default     = ""
}

variable "google_site_verification" {
  description = "Optional Google Search Console verification code. Plumbed as GOOGLE_SITE_VERIFICATION env var so index.html can inject the meta tag."
  type        = string
  default     = ""
}
