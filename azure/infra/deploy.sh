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
# RS_ALERTS_WEBHOOK_URL and RS_ALERTS_GTI_PROJECT. RS Alerts delivers via a
# Teams incoming webhook (channel -> Workflows -> "Post to a channel when a
# webhook request is received" -> copy the webhook URL) rather than the bot
# itself, so no Bot Framework/Azure AD credentials are needed for delivery:
#   export ENABLE_RS_ALERTS=true
#   export RS_ALERTS_WEBHOOK_URL=https://.../workflows/.../triggers/manual/paths/invoke?...
#   export RS_ALERTS_GTI_PROJECT=your-gti-project-id
#
# Optional bot-wide response formatting instructions (empty by default — see
# main.bicep's outputFormatInstructions param / app/output_format_store.py):
#   export OUTPUT_FORMAT_INSTRUCTIONS="Show severity as bold text instead of emoji"
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
if [ "$ENABLE_RS_ALERTS" = "true" ] && { [ -z "${RS_ALERTS_WEBHOOK_URL:-}" ] || [ -z "${RS_ALERTS_GTI_PROJECT:-}" ]; }; then
  echo "ENABLE_RS_ALERTS=true requires RS_ALERTS_WEBHOOK_URL and RS_ALERTS_GTI_PROJECT" >&2
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
  EXTRA_PARAMS+=(--parameters "enableRsAlerts=true" "rsAlertsWebhookUrl=$RS_ALERTS_WEBHOOK_URL" "rsAlertsGtiProject=$RS_ALERTS_GTI_PROJECT")
  
  if [ -n "${RSA_FUNCTION_NAME:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsFunctionAppName=$RSA_FUNCTION_NAME")
  fi
  if [ -n "${RSA_SCHEDULE:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsSchedule=$RSA_SCHEDULE")
  fi
  if [ -n "${RSA_SCHEDULE_TIMEZONE:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsScheduleTimezone=$RSA_SCHEDULE_TIMEZONE")
  fi
  if [ -n "${RSA_BACKFILL_DAYS:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsBackfillDays=$RSA_BACKFILL_DAYS")
  fi
  if [ -n "${RSA_PAGE_SIZE:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsPageSize=$RSA_PAGE_SIZE")
  fi
  if [ -n "${RSA_FUNCTION_MEMORY:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsInstanceMemoryMB=$RSA_FUNCTION_MEMORY")
  fi
  if [ -n "${RSA_FILTER_SEVERITY_LEVEL:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsFilterSeverityLevel=$RSA_FILTER_SEVERITY_LEVEL")
  fi
  if [ -n "${RSA_FILTER_PRIORITY_LEVEL:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsFilterPriorityLevel=$RSA_FILTER_PRIORITY_LEVEL")
  fi
  if [ -n "${RSA_FILTER_RELEVANCE_LEVEL:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsFilterRelevanceLevel=$RSA_FILTER_RELEVANCE_LEVEL")
  fi
  if [ -n "${RSA_FILTER_RELEVANCE_CONFIDENCE:-}" ]; then
    EXTRA_PARAMS+=(--parameters "rsAlertsFilterRelevanceConfidence=$RSA_FILTER_RELEVANCE_CONFIDENCE")
  fi
fi
if [ -n "${OUTPUT_FORMAT_INSTRUCTIONS:-}" ]; then
  EXTRA_PARAMS+=(--parameters "outputFormatInstructions=$OUTPUT_FORMAT_INSTRUCTIONS")
fi

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$SCRIPT_DIR/main.bicep" \
  --parameters "$SCRIPT_DIR/main.parameters.json" \
  --parameters functionAppName="$FUNCTION_APP_NAME" \
  --parameters gtiApiKey="$GTI_API_KEY" \
  "${EXTRA_PARAMS[@]}"
