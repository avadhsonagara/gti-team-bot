#!/usr/bin/env bash
# =============================================================================
# Deploys infra/main.bicep.
#
# The Teams app manifest is built by the template itself at deploy time
# (manifestSourceBaseUrl, default: this repo's teams-app-manifest/ folder) —
# it fetches manifest.json + icons, rewrites manifest.json's id/botId to this
# deployment's own bot App ID, and zips the result. Nothing to package locally.
#
# Bot authentication uses a User-Assigned Managed Identity (Azure Bot's
# "UserAssignedMSI" app type) instead of an app registration + client secret,
# so there's no CLIENT_ID / CLIENT_SECRET to supply — the identity's client ID
# becomes the bot's App ID automatically. The only secret this script needs to
# pass through (rather than commit to main.parameters.json) is GTI_API_KEY.
#
# Usage:
#   export GTI_API_KEY=...
#   ./deploy.sh <resource-group> [functionAppName] [storageAccountName] [existingAppServicePlanName]
#
# Omit storageAccountName to let Bicep generate a new storage account; pass
# an existing one to have this deployment adopt it instead.
#
# Omit existingAppServicePlanName to create a new Flex Consumption plan; pass
# the name of an existing plan to reuse it instead — required when
# functionAppName already exists on a plan Bicep didn't create (a Function
# App cannot be moved between Flex Consumption plans in-place).
#
# RS Alerts (background GTI Alerts -> Teams Function App) is off by default.
# Turn it on by exporting ENABLE_RS_ALERTS=true along with
# RS_ALERTS_TEAMS_CHANNEL_ID and RS_ALERTS_GTI_PROJECT:
#   export ENABLE_RS_ALERTS=true
#   export RS_ALERTS_TEAMS_CHANNEL_ID=https://teams.microsoft.com/l/channel/19%3a...
#   export RS_ALERTS_GTI_PROJECT=your-gti-project-id
# =============================================================================
set -euo pipefail

RESOURCE_GROUP="${1:?Usage: ./deploy.sh <resource-group> [functionAppName] [storageAccountName] [existingAppServicePlanName]}"
FUNCTION_APP_NAME="${2:-gti-team-bot}"
STORAGE_ACCOUNT_NAME="${3:-}"
EXISTING_PLAN_NAME="${4:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${GTI_API_KEY:-}" ]; then
  echo "Missing required environment variable: GTI_API_KEY" >&2
  exit 1
fi

ENABLE_RS_ALERTS="${ENABLE_RS_ALERTS:-false}"
if [ "$ENABLE_RS_ALERTS" = "true" ] && { [ -z "${RS_ALERTS_TEAMS_CHANNEL_ID:-}" ] || [ -z "${RS_ALERTS_GTI_PROJECT:-}" ]; }; then
  echo "ENABLE_RS_ALERTS=true requires RS_ALERTS_TEAMS_CHANNEL_ID and RS_ALERTS_GTI_PROJECT" >&2
  exit 1
fi

EXTRA_PARAMS=()
if [ -n "$STORAGE_ACCOUNT_NAME" ]; then
  EXTRA_PARAMS+=(--parameters "storageAccountName=$STORAGE_ACCOUNT_NAME")
fi
if [ -n "$EXISTING_PLAN_NAME" ]; then
  EXTRA_PARAMS+=(--parameters "appServicePlanName=$EXISTING_PLAN_NAME" "createAppServicePlan=false")
fi
if [ "$ENABLE_RS_ALERTS" = "true" ]; then
  EXTRA_PARAMS+=(--parameters "enableRsAlerts=true" "rsAlertsTeamsChannelId=$RS_ALERTS_TEAMS_CHANNEL_ID" "rsAlertsGtiProject=$RS_ALERTS_GTI_PROJECT")
fi

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$SCRIPT_DIR/main.bicep" \
  --parameters "$SCRIPT_DIR/main.parameters.json" \
  --parameters functionAppName="$FUNCTION_APP_NAME" \
  --parameters gtiApiKey="$GTI_API_KEY" \
  "${EXTRA_PARAMS[@]}"
