#!/usr/bin/env bash
# Copy secret values from the old flat single-tenant names into the new
# tenant-namespaced names, so the multi-tenant refactor doesn't cost you a
# round of re-pasting API keys.
#
#   Before: ANTHROPIC_API_KEY
#   After:  default_ANTHROPIC_API_KEY
#
# Usage:
#   ./scripts/migrate_secrets.sh <tenant_id>
#   ./scripts/migrate_secrets.sh default
#
# Run this AFTER `terraform apply` has created the new namespaced slots
# (they'll hold REPLACE_ME placeholders) and BEFORE you delete the old ones.
# Safe to re-run; each run just adds another version.

set -euo pipefail

TENANT="${1:-}"
if [[ -z "$TENANT" ]]; then
  echo "usage: $0 <tenant_id>" >&2
  echo "example: $0 default" >&2
  exit 1
fi

SECRETS=(
  ANTHROPIC_API_KEY
  ELEVENLABS_API_KEY
  ELEVENLABS_VOICE_ID
  TAVILY_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_NOTIFY_CHAT_ID
  KAGGLE_API_TOKEN
  LANGSMITH_API_KEY
  LANGSMITH_PROJECT
  LANGSMITH_ENDPOINT
  LANGSMITH_TRACING
  VOYAGE_API_KEY
  OPENAI_API_KEY
)

echo "Migrating secrets into tenant namespace: ${TENANT}"
echo

migrated=0
skipped=0

for name in "${SECRETS[@]}"; do
  old="$name"
  new="${TENANT}_${name}"

  # Old secret must exist and have at least one version
  if ! gcloud secrets describe "$old" >/dev/null 2>&1; then
    echo "  skip  ${old} → not found"
    skipped=$((skipped + 1))
    continue
  fi

  value="$(gcloud secrets versions access latest --secret="$old" 2>/dev/null || true)"

  if [[ -z "$value" || "$value" == "REPLACE_ME" ]]; then
    echo "  skip  ${old} → empty or still a placeholder"
    skipped=$((skipped + 1))
    continue
  fi

  if ! gcloud secrets describe "$new" >/dev/null 2>&1; then
    echo "  skip  ${new} → target slot doesn't exist (run terraform apply first)"
    skipped=$((skipped + 1))
    continue
  fi

  printf '%s' "$value" | gcloud secrets versions add "$new" --data-file=- >/dev/null
  echo "  ok    ${old} → ${new}"
  migrated=$((migrated + 1))
done

echo
echo "migrated: ${migrated}   skipped: ${skipped}"
echo
echo "Next: force a new Cloud Run revision so the container picks up the values:"
echo "  gcloud run services update shelby-${TENANT} --region=<region> --update-labels=secrets-migrated=\$(date +%s)"
echo
echo "Once you've confirmed the new deploy works, delete the old flat secrets:"
for name in "${SECRETS[@]}"; do
  echo "  gcloud secrets delete ${name} --quiet"
done
