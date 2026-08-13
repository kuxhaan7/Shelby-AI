# Remote state lives in a GCS bucket so multiple people (or CI) can share it
# and Terraform state isn't sitting on your laptop where it can get lost.
#
# Chicken-and-egg: the bucket has to exist before the backend can point at it.
# So the flow is:
#   1. First apply runs with LOCAL state (this block commented out).
#   2. That first apply creates the state bucket (see main.tf).
#   3. Uncomment this block, set the bucket name, then run:
#         terraform init -migrate-state
#      Terraform copies your local .tfstate into GCS and hands over.
#   4. Delete the local terraform.tfstate — GCS is the source of truth now.

# terraform {
#   backend "gcs" {
#     bucket = "shelby-tfstate-<your-project-id>"
#     prefix = "shelby"
#   }
# }
