#!/usr/bin/env bash
# =============================================================================
# Deploys infra/main.bicep.
#
# The Teams app manifest zip is fetched by the template itself at deploy time
# (manifestZipUrl, default: this repo's raw GitHub URL for
# teams-app-manifest/teams-app-manifest.zip) — nothing to package locally.
#
# This script's only job Bicep can't do itself: read secrets from environment
# variables instead of committing them to main.parameters.json.
#
# Required environment variables (secrets — not read from any file):
#   CLIENT_ID, CLIENT_SECRET, TENANT_ID, GTI_API_KEY
#
# Usage:
#   export CLIENT_ID=... CLIENT_SECRET=... TENANT_ID=... GTI_API_KEY=...
#   ./deploy.sh <resource-group> [functionAppName] [storageAccountName] [existingAppServicePlanName]
#
# Omit storageAccountName to let Bicep generate a new storage account; pass
# an existing one to have this deployment adopt it instead.
#
# Omit existingAppServicePlanName to create a new Flex Consumption plan; pass
# the name of an existing plan to reuse it instead — required when
# functionAppName already exists on a plan Bicep didn't create (a Function
# App cannot be moved between Flex Consumption plans in-place).
# =============================================================================
set -euo pipefail

RESOURCE_GROUP="${1:?Usage: ./deploy.sh <resource-group> [functionAppName] [storageAccountName] [existingAppServicePlanName]}"
FUNCTION_APP_NAME="${2:-gti-team-bot}"
STORAGE_ACCOUNT_NAME="${3:-}"
EXISTING_PLAN_NAME="${4:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for var in CLIENT_ID CLIENT_SECRET TENANT_ID GTI_API_KEY; do
  if [ -z "${!var:-}" ]; then
    echo "Missing required environment variable: $var" >&2
    exit 1
  fi
done

EXTRA_PARAMS=()
if [ -n "$STORAGE_ACCOUNT_NAME" ]; then
  EXTRA_PARAMS+=(--parameters "storageAccountName=$STORAGE_ACCOUNT_NAME")
fi
if [ -n "$EXISTING_PLAN_NAME" ]; then
  EXTRA_PARAMS+=(--parameters "appServicePlanName=$EXISTING_PLAN_NAME" "createAppServicePlan=false")
fi

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$SCRIPT_DIR/main.bicep" \
  --parameters "$SCRIPT_DIR/main.parameters.json" \
  --parameters functionAppName="$FUNCTION_APP_NAME" \
  --parameters clientId="$CLIENT_ID" \
  --parameters clientSecret="$CLIENT_SECRET" \
  --parameters tenantId="$TENANT_ID" \
  --parameters gtiApiKey="$GTI_API_KEY" \
  "${EXTRA_PARAMS[@]}"
