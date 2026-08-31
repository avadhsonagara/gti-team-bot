#!/usr/bin/env bash
# =============================================================================
# GTI Teams Bot (Agentic) — GCP Automated Deployment Script
# =============================================================================
# Usage:
#   export GTI_API_KEY="your-gti-api-key"
#   ./deploy.sh [project_id] [region] [bot_name]
#
# Optional RS Alerts:
#   export ENABLE_RS_ALERTS=true
#   export RS_ALERTS_TEAMS_CHANNEL_ID="19:...@thread.tacv2"
#   export RS_ALERTS_GTI_PROJECT="your-gti-project-id"
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GCP_PROJECT_ID:-gtimsteamaiintegration-3898}}"
REGION="${2:-${GCP_REGION:-us-central1}}"
BOT_NAME="${3:-${BOT_NAME:-gti-team-bot}}"

echo "============================================================"
echo " Deploying GTI Teams Bot to Google Cloud Platform"
echo " Project: $PROJECT_ID | Region: $REGION | Bot: $BOT_NAME"
echo "============================================================"

# Validate required secrets
if [ -z "${GTI_API_KEY:-}" ]; then
  echo "Error: Missing required environment variable: GTI_API_KEY" >&2
  exit 1
fi

ENABLE_RS_ALERTS="${ENABLE_RS_ALERTS:-false}"
if [ "$ENABLE_RS_ALERTS" = "true" ]; then
  if [ -z "${RS_ALERTS_TEAMS_CHANNEL_ID:-}" ]; then
    echo "Error: ENABLE_RS_ALERTS=true requires RS_ALERTS_TEAMS_CHANNEL_ID" >&2
    exit 1
  fi
fi

cd "$SCRIPT_DIR"

echo "Step 1: Initializing Terraform..."
terraform init -upgrade

echo "Step 2: Planning & Applying Terraform configuration..."
TF_VARS=(
  -var="project_id=$PROJECT_ID"
  -var="region=$REGION"
  -var="bot_name=$BOT_NAME"
  -var="gti_api_key=$GTI_API_KEY"
)

if [ "$ENABLE_RS_ALERTS" = "true" ]; then
  TF_VARS+=(
    -var="enable_rs_alerts=true"
    -var="rs_alerts_teams_channel_id=$RS_ALERTS_TEAMS_CHANNEL_ID"
  )
  if [ -n "${RSA_SCHEDULE:-}" ]; then
    TF_VARS+=(-var="rsa_schedule=$RSA_SCHEDULE")
  fi
  if [ -n "${RSA_FUNCTION_NAME:-}" ]; then
    TF_VARS+=(-var="rsa_function_name=$RSA_FUNCTION_NAME")
  fi
  if [ -n "${RSA_BACKFILL_DAYS:-}" ]; then
    TF_VARS+=(-var="rsa_backfill_days=$RSA_BACKFILL_DAYS")
  fi
fi

if [ -n "${OUTPUT_FORMAT_INSTRUCTIONS:-}" ]; then
  TF_VARS+=(-var="output_format_instructions=$OUTPUT_FORMAT_INSTRUCTIONS")
fi

terraform apply -auto-approve "${TF_VARS[@]}"

echo ""
echo "============================================================"
echo " Deployment Complete!"
echo "============================================================"
echo "Messaging Endpoint: $(terraform output -raw gti_bot_messaging_endpoint)"
echo "Teams Manifest ZIP: $(terraform output -raw teams_manifest_zip_gcs_url)"
echo "============================================================"
