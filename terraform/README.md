# Shelby on GCP, via Terraform

This directory provisions everything Shelby needs to run on GCP: APIs,
Artifact Registry, a data bucket, Secret Manager slots, a runtime service
account, and a Cloud Run service. One `terraform apply` gives you a live URL.

Follow it top to bottom the first time. Each step is small on purpose.

## 1. Install the CLIs

```bash
# macOS
brew install terraform google-cloud-sdk

# Or download from:
#   https://developer.hashicorp.com/terraform/install
#   https://cloud.google.com/sdk/docs/install
```

Verify:

```bash
terraform -version   # >= 1.6
gcloud --version
```

## 2. Create a GCP project

Do this once, in the console: https://console.cloud.google.com/projectcreate

Pick an ID like `shelby-dev-<yourname>`. Link a billing account (Cloud Run
and Artifact Registry both need it, but stay in the free tier for a solo
project).

Then, on your laptop:

```bash
gcloud auth login
gcloud auth application-default login   # this is what Terraform picks up
gcloud config set project shelby-dev-<yourname>
```

## 3. Fill in tfvars

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars, set project_id
```

## 4. First apply (local state)

```bash
terraform init
terraform plan     # read the plan — this is the whole point of Terraform
terraform apply    # type 'yes' when it asks
```

This takes 3–5 minutes the first time (most of it is enabling APIs). When
it's done, Terraform prints a `tenants` map, one entry per tenant:

```bash
terraform output tenants
# {
#   "default" = {
#     "service_url" = "https://shelby-default-xxxx-uc.a.run.app"
#     "data_bucket" = "shelby-data-default-<project>"
#     ...
#   }
# }

# Single value, no quotes:
terraform output -json tenants | jq -r '.default.service_url'
```

Open that URL. You'll see the GCP "hello" placeholder page, which is
expected: Shelby isn't built and pushed yet.

## 5. Rotate the placeholder secrets to real values

Terraform bootstrapped every secret with a `REPLACE_ME` placeholder so
Cloud Run could start. Now overwrite each one with your real key.

**Secret IDs are namespaced per tenant.** The container still sees
`ANTHROPIC_API_KEY`; the Secret Manager slot underneath is
`<tenant>_ANTHROPIC_API_KEY`. That's what keeps tenant A from reading
tenant B's keys.

```bash
# Repeat for every secret you actually use, per tenant
printf 'sk-ant-your-key' | gcloud secrets versions add default_ANTHROPIC_API_KEY --data-file=-
printf 'sk_your-elevenlabs-key' | gcloud secrets versions add default_ELEVENLABS_API_KEY --data-file=-
```

Use `printf`, not `echo` — `echo` appends a newline that some APIs reject.

The env-var names are in `variables.tf` under `managed_secrets`. Real
values never enter Terraform state; `ignore_changes = [secret_data]` on
the placeholder version means re-apply won't clobber what you set.

**Migrating from a single-tenant deploy?** Don't re-paste anything —
`scripts/migrate_secrets.sh` copies values from the old flat names into
the namespaced ones:

```bash
./scripts/migrate_secrets.sh default
```

## 6. Build and push the Shelby container

From the repo root (one level up from this directory):

```bash
IMAGE=$(cd terraform && terraform output -raw image_repo)/shelby:latest

# Cloud Build is the simplest path — no local Docker required
gcloud builds submit --tag "$IMAGE" .

# Or, if you prefer local Docker:
# gcloud auth configure-docker us-central1-docker.pkg.dev
# docker build -t "$IMAGE" .
# docker push "$IMAGE"
```

## 7. Point Terraform at the real image

Edit `terraform.tfvars`, uncomment and set:

```hcl
image = "us-central1-docker.pkg.dev/<project>/shelby/shelby:latest"
```

Then:

```bash
terraform apply
```

Cloud Run swaps every tenant to the new image with a rolling update.
Reopen the tenant's `service_url` — real Shelby.

## 8. Migrate state to GCS (recommended)

Local `terraform.tfstate` is fine for solo work but risky (one accidental
`rm` and you can't cleanly update anything). Move it to the state bucket
Terraform already created:

1. Edit `backend.tf`, uncomment the `terraform { backend "gcs" { ... } }`
   block, and set `bucket` to the value of `terraform output -raw tfstate_bucket`.
2. Run:

   ```bash
   terraform init -migrate-state
   # answers 'yes' to the prompt
   rm terraform.tfstate terraform.tfstate.backup   # GCS is authoritative now
   ```

Now anyone with GCP access can `terraform apply` from anywhere and they
share the same state.

## Multi-tenancy

The root module iterates `var.tenants` and calls `modules/tenant` once
per entry. Each call stands up a fully isolated Shelby:

| Resource | Naming | Isolation |
|----------|--------|-----------|
| Cloud Run service | `shelby-<tenant>` | separate service, separate URL |
| Data bucket | `shelby-data-<tenant>-<project>` | bucket-scoped IAM |
| Runtime service account | `shelby-<tenant>-runtime` | one SA per tenant |
| Secrets | `<tenant>_ANTHROPIC_API_KEY` | `secretAccessor` on own set only |

Tenants **share** the container image (one build, one Artifact Registry
repo) and **isolate** everything stateful. A tenant's service account has
`objectAdmin` on exactly one bucket and `secretAccessor` on exactly its
own namespaced secrets — so even a fully compromised tenant container
cannot read another tenant's data or API keys.

### Add a tenant

```hcl
# terraform.tfvars
tenants = ["default", "acme"]
```

```bash
terraform apply
```

That's the whole onboarding flow. Terraform creates acme's service,
bucket, SA, and 11 namespaced secret slots. Then populate acme's keys:

```bash
printf 'sk-ant-acme-key' | gcloud secrets versions add acme_ANTHROPIC_API_KEY --data-file=-
```

### Remove a tenant

Drop the id from `tenants` and apply. Terraform destroys only that
tenant's resources; everyone else is untouched. The data bucket has
`force_destroy = false`, so a bucket with objects in it will refuse to
delete — deliberate, so a typo in the tenant list can't wipe customer
data.

### Per-tenant custom domains

```hcl
custom_domains = {
  default = "shelby.is-a.dev"
  acme    = "acme.shelby.is-a.dev"
}
```

Each domain still needs its own CNAME to `ghs.googlehosted.com` and
Search Console verification. See `docs/GCP_MIGRATION.md`.

## Common commands

```bash
terraform plan                                          # show what would change
terraform apply                                         # make it so
terraform destroy                                       # tear it all down (careful)
terraform output tenants                                # all tenants + their URLs
terraform output -json tenants | jq -r '.default.service_url'
terraform state list                                    # every managed resource
terraform state show 'module.tenant["acme"].google_cloud_run_v2_service.tenant'
```

## What comes next (Phase 3+)

- Wire Shelby's own tool loop to `terraform_plan` / `terraform_apply` so
  the agent can propose infra changes for human approval.
- Per-tenant data-access policies inside the app: scope RAG queries and
  memory reads by `SHELBY_TENANT_ID` (already plumbed as an env var), and
  write an audit log per tenant.

See `docs/GCP_MIGRATION.md` for the sequencing.
