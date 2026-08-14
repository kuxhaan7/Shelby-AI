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
Two project-level roles added on top of the existing `roles/owner`:
- `roles/serviceusage.serviceUsageConsumer` — fixes the ADC quota-project
  warning so gcloud can enable APIs on our behalf.
- `roles/storage.objectAdmin` — read/write on every bucket in the project,
  needed for manual inspection / uploads during Phase 1 iteration.

Both are technically redundant against Owner but were granted explicitly
before I realized Owner already covers them. Harmless; can be removed
whenever. The Compute Engine default service account also picked up
Editor + Storage Object Admin + Service Usage Consumer — that's GCP
default behavior. On a real customer deploy the CE default SA gets
disabled entirely and everything runs through named service accounts.

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

### Custom domain — shelby.is-a.dev

Registering a free `.is-a.dev` subdomain via
[is-a-dev/register](https://github.com/is-a-dev/register) so the
public URL isn't `shelby-xa3v4wt7na-uc.a.run.app`.

**PR filed** — 2026-08-13
`is-a-dev/register#47205` requesting `shelby.is-a.dev`, CNAME to
`ghs.googlehosted.com`. First submission failed CI because the JSON
used `record` (singular) instead of `records` (plural) per the schema;
pushed a fix on the same branch and CI is passing. Waiting on maintainer
review (typical 1–3 days).

**Terraform prepared for the mapping** — this commit
- `var.custom_domain` gates a `google_cloud_run_domain_mapping` resource
  so it's a no-op until we set it. Once DNS is live and the domain is
  verified in Google Search Console, setting `custom_domain =
  "shelby.is-a.dev"` in tfvars and running apply issues a managed
  TLS cert automatically.
- `var.google_site_verification` plumbs an env var into the container.
  The `/` route reads it and injects the `<meta
  name="google-site-verification">` tag Google Search Console needs to
  verify domain ownership — no code push required, just a re-apply.

**Post-merge checklist (once the PR merges):**
1. Wait ~15 min for is-a.dev DNS to propagate.
2. Add `shelby.is-a.dev` as a property in Google Search Console →
   pick HTML meta tag verification method → copy the code.
3. Set `google_site_verification = "..."` in `terraform.tfvars` and
   `terraform apply`. Cloud Run rolls a new revision serving the tag.
4. Click **Verify** in Search Console.
5. Set `custom_domain = "shelby.is-a.dev"` in `terraform.tfvars` and
   `terraform apply` again. Domain mapping is created; managed cert
   issues in ~10 min.
6. `https://shelby.is-a.dev` serves Shelby with a real TLS cert.

## Phase 2 — Multi-tenant refactor

Not started.

## Phase 3 — Agent-managed Terraform

Not started.

## Phase 4 — Data access policies

Not started.

## Phase 5 — TB-scale extraction

Not started.
