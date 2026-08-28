#!/usr/bin/env bash
# =============================================================================
# Deploys infra/main.bicep and uploads the Teams app manifest zip.
#
# Bicep has no access to local files at deploy time, so this script does the
# two things Bicep itself can't:
#   1. Zips teams-app-manifest/ (manifest.json + icons) and base64-encodes it.
#   2. Passes secrets from environment variables instead of committing them
#      to main.parameters.json.
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
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
MANIFEST_DIR="$REPO_ROOT/teams-app-manifest"

for var in CLIENT_ID CLIENT_SECRET TENANT_ID GTI_API_KEY; do
  if [ -z "${!var:-}" ]; then
    echo "Missing required environment variable: $var" >&2
    exit 1
  fi
done

MANIFEST_ZIP_BASE64=""
if [ -d "$MANIFEST_DIR" ]; then
  TMP_ZIP="$(mktemp -t teams-manifest-XXXXXX.zip)"
  trap 'rm -f "$TMP_ZIP"' EXIT
  python3 - "$MANIFEST_DIR" "$TMP_ZIP" <<'PY'
import sys, zipfile, pathlib
src, dest = pathlib.Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted(src.iterdir()):
        if f.is_file():
            zf.write(f, arcname=f.name)
PY
  MANIFEST_ZIP_BASE64="$(base64 -w0 "$TMP_ZIP")"
  echo "Packaged Teams manifest ($(du -h "$TMP_ZIP" | cut -f1)) from $MANIFEST_DIR"
else
  echo "Warning: $MANIFEST_DIR not found — deploying without a manifest upload." >&2
fi

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
  --parameters secureAppSettings="{\"CLIENT_ID\":\"$CLIENT_ID\",\"CLIENT_SECRET\":\"$CLIENT_SECRET\",\"TENANT_ID\":\"$TENANT_ID\",\"GTI_API_KEY\":\"$GTI_API_KEY\"}" \
  --parameters manifestZipBase64="$MANIFEST_ZIP_BASE64" \
  "${EXTRA_PARAMS[@]}"
