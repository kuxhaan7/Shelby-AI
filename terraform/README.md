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
it's done, Terraform prints the outputs — the important one is
`service_url`. Open it in a browser. You'll see the GCP "hello" placeholder
page, which is expected: Shelby isn't built and pushed yet.

## 5. Rotate the placeholder secrets to real values

Terraform bootstrapped every secret with a `REPLACE_ME` placeholder so
Cloud Run could start. Now overwrite each one with your real key. This
creates a new version in Secret Manager; Cloud Run picks up the new
`latest` automatically on the next revision.

```bash
# Repeat for every secret you actually use
printf 'sk-ant-your-key' | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
printf 'sk_your-elevenlabs-key' | gcloud secrets versions add ELEVENLABS_API_KEY --data-file=-
```

The list of slots Terraform created is in `variables.tf` under
`managed_secrets`. Real secret values never enter Terraform state —
`ignore_changes = [secret_data]` on the placeholder version means re-apply
won't overwrite whatever you set with gcloud.

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

Cloud Run swaps to the new image with a rolling update. Reopen
`service_url` — real Shelby.

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

## Common commands

```bash
terraform plan                          # show what would change
terraform apply                         # make it so
terraform destroy                       # tear it all down (careful)
terraform output                        # reprint all outputs
terraform output -raw service_url       # single value, no quotes
terraform state list                    # every resource Terraform manages
terraform state show <resource>         # detail on one resource
```

## What comes next (Phase 2+)

- Refactor into a reusable `tenant` module and iterate over
  `var.tenants` to stand up N isolated Shelby instances at once.
- Add per-tenant IAM so tenant A's service account cannot see tenant B's
  bucket or secrets.
- Wire Shelby's own tool loop to `terraform_plan` / `terraform_apply` so
  the agent can propose infra changes for human approval.

See the top-level plan for the sequencing.
