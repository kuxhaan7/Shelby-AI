# Shelby on GCP — migration milestones

Log of the Railway → GCP migration, organized by phase. Each entry is a
concrete thing that got done, dated, with the artifact (commit, resource,
or console screenshot).

The plan lives in the top-level FDE-prep doc; this file is the "what
actually happened" log.

## Phase 1 — Single-tenant on GCP via Terraform

**Goal:** replace the Railway deploy with a Terraform-managed Cloud Run
service serving the real Shelby container, backed by GCS for data and
Secret Manager for API keys.

### Milestones

**Terraform scaffolded** — `3db18e5`
9 files under `terraform/`: providers pinned, backend prepared for GCS
migration, one Cloud Run v2 service with GCS Fuse volume, Secret Manager
slots for every managed key, dedicated runtime service account with
least-privilege IAM. README walks through the 8 steps from empty machine
to live URL.

**Secret bootstrap fix** — `87ee4e2`
First `terraform apply` failed because Cloud Run refuses to boot when a
referenced secret has zero versions. Fixed by having Terraform seed each
secret with a `REPLACE_ME` placeholder version, so `latest` always
resolves. Real values overwrite the placeholder via `gcloud secrets
versions add`; `lifecycle.ignore_changes = [secret_data]` keeps
subsequent applies from reverting.

**Dockerfile entrypoint fix** — `3f7afe4`
Old `CMD` ran the Telegram bot only, no HTTP port. Railway hid this with
a `startCommand` override in `railway.toml`; Cloud Run has no equivalent
and would loop-restart the container. Switched the default `CMD` to
`uvicorn shelby.api.main:app`, which serves the web UI and spawns the
Telegram bot as a background task in the same event loop.

**GCP project created + billing linked** — 2026-08-13
Project `shelby-505403` in `us-central1`, billing linked. `gcloud config`
and ADC pointed at it.

**IAM grant on `kushaankaushik007@gmail.com`** — 2026-08-13
Two project-level roles:
- `roles/serviceusage.serviceUsageConsumer` — fixes the ADC quota-project
  warning so gcloud can enable APIs on our behalf.
- `roles/storage.objectAdmin` — read/write on every bucket in the project,
  needed for manual inspection / uploads during Phase 1 iteration.

Follow-up in Phase 2: scope Storage Object Admin down to per-tenant
buckets so a tenant's principal cannot touch another tenant's data.

**First `terraform apply` succeeded** — 2026-08-13
All Phase 1 resources provisioned: APIs enabled, Artifact Registry,
runtime SA, data bucket, tfstate bucket, 11 Secret Manager slots with
placeholders, Cloud Run service serving the `cloudrun/hello` placeholder
image. Public invoker binding on so the URL loads in a browser.

### Pending in Phase 1

- Rotate the 3 essential placeholder secrets to real values
  (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `ELEVENLABS_API_KEY`).
- Build the real Shelby container via `gcloud builds submit` and push to
  Artifact Registry.
- Update `terraform.tfvars` with the pushed image tag, re-apply,
  Cloud Run rolls to real Shelby.
- (Optional) Migrate Terraform state to the GCS bucket for portability.

## Phase 2 — Multi-tenant refactor

Not started.

## Phase 3 — Agent-managed Terraform

Not started.

## Phase 4 — Data access policies

Not started.

## Phase 5 — TB-scale extraction

Not started.
