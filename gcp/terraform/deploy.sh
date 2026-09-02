#!/usr/bin/env bash
# =============================================================================
# GTI Teams Bot (Agentic) — GCP Automated Deployment Script
# =============================================================================
# Usage:
#   export GTI_API_KEY="your-gti-api-key"
#   ./deploy.sh [project_id] [region] [bot_name]
#
# Optional RS Alerts. Pass the FULL channel link (not a bare ID) so the
# bot's Teams app can be auto-installed into the team via Microsoft Graph:
#   export ENABLE_RS_ALERTS=true
#   export RS_ALERTS_TEAMS_CHANNEL_ID="https://teams.microsoft.com/l/channel/19%3a...?groupId=..."
#   export RSA_GTI_PROJECT="your-gti-project-id"
#
# Optional RS Alerts tuning:
#   export RSA_SCHEDULE="*/3 * * * *"
#   export RSA_SCHEDULE_TIMEZONE="Etc/UTC"
#   export RSA_FUNCTION_NAME="gti-alerts-fetch"
#   export RSA_PAGE_SIZE=1000
#   export RSA_FUNCTION_MEMORY="256Mi"
#   export RSA_FUNCTION_TIMEOUT_SECONDS=540
#   export RSA_FILTER_SEVERITY_LEVEL="MEDIUM,HIGH"
#   export RSA_FILTER_PRIORITY_LEVEL="MEDIUM,HIGH,CRITICAL"
#   export RSA_FILTER_RELEVANCE_LEVEL="MEDIUM,HIGH"
#   export RSA_FILTER_RELEVANCE_CONFIDENCE="MEDIUM,HIGH"
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
  if [ -z "${RSA_GTI_PROJECT:-}" ]; then
    echo "Error: ENABLE_RS_ALERTS=true requires RSA_GTI_PROJECT (the GTI project to query for alerts — not necessarily the same as the GCP hosting project)" >&2
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
    -var="rsa_gti_project=$RSA_GTI_PROJECT"
  )
  if [ -n "${RSA_SCHEDULE:-}" ]; then
    TF_VARS+=(-var="rsa_schedule=$RSA_SCHEDULE")
  fi
  if [ -n "${RSA_SCHEDULE_TIMEZONE:-}" ]; then
    TF_VARS+=(-var="rsa_schedule_timezone=$RSA_SCHEDULE_TIMEZONE")
  fi
  if [ -n "${RSA_FUNCTION_NAME:-}" ]; then
    TF_VARS+=(-var="rsa_function_name=$RSA_FUNCTION_NAME")
  fi
  if [ -n "${RSA_PAGE_SIZE:-}" ]; then
    TF_VARS+=(-var="rsa_page_size=$RSA_PAGE_SIZE")
  fi
  if [ -n "${RSA_FUNCTION_MEMORY:-}" ]; then
    TF_VARS+=(-var="rsa_function_memory=$RSA_FUNCTION_MEMORY")
  fi
  if [ -n "${RSA_FUNCTION_TIMEOUT_SECONDS:-}" ]; then
    TF_VARS+=(-var="rsa_function_timeout_seconds=$RSA_FUNCTION_TIMEOUT_SECONDS")
  fi
  if [ -n "${RSA_FILTER_SEVERITY_LEVEL:-}" ]; then
    TF_VARS+=(-var="rsa_filter_severity_level=$RSA_FILTER_SEVERITY_LEVEL")
  fi
  if [ -n "${RSA_FILTER_PRIORITY_LEVEL:-}" ]; then
    TF_VARS+=(-var="rsa_filter_priority_level=$RSA_FILTER_PRIORITY_LEVEL")
  fi
  if [ -n "${RSA_FILTER_RELEVANCE_LEVEL:-}" ]; then
    TF_VARS+=(-var="rsa_filter_relevance_level=$RSA_FILTER_RELEVANCE_LEVEL")
  fi
  if [ -n "${RSA_FILTER_RELEVANCE_CONFIDENCE:-}" ]; then
    TF_VARS+=(-var="rsa_filter_relevance_confidence=$RSA_FILTER_RELEVANCE_CONFIDENCE")
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
