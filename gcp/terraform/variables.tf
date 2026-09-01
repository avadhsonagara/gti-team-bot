# =============================================================================
# GTI Teams Bot (Agentic) — GCP Terraform Variables
# =============================================================================

variable "project_id" {
  description = "Google Cloud Project ID where all resources will be created."
  type        = string
  default     = "gtimsteamaiintegration-3898"
}

variable "region" {
  description = "Google Cloud region for all resources (Cloud Functions, GCS, Firestore, Secrets)."
  type        = string
  default     = "us-central1"
}

variable "bot_name" {
  description = "Base resource name for the bot application."
  type        = string
  default     = "gti-team-bot"
}

variable "python_runtime" {
  description = "Python worker runtime version for Cloud Run functions."
  type        = string
  default     = "python311"
}

variable "memory" {
  description = "Memory allocated to the gti-bot Cloud Run function (e.g. 512Mi, 1Gi, 2Gi)."
  type        = string
  default     = "1Gi"
}

variable "timeout_seconds" {
  description = "Execution timeout in seconds for the gti-bot function (max 3600 for HTTP)."
  type        = number
  default     = 300
}

variable "max_instances" {
  description = "Maximum scale-out instance count for the gti-bot function."
  type        = number
  default     = 100
}

variable "min_instances" {
  description = "Minimum idle instance count for warm starts (0 allows scaling to zero)."
  type        = number
  default     = 0
}


# ── Google Threat Intelligence (GTI) Credentials ─────────────────────────────

variable "gti_api_key" {
  description = "Google Threat Intelligence / VirusTotal API key. Stored securely in Secret Manager."
  type        = string
  sensitive   = true
}

variable "gti_api_base_url" {
  description = "Base URL for the Google Threat Intelligence Agentic API."
  type        = string
  default     = "https://www.virustotal.com/api/v3"
}

# ── Firestore & Output Formatting ───────────────────────────────────────────

variable "create_firestore_database" {
  description = "Set to true to create the default Firestore database in Native mode if it does not already exist."
  type        = bool
  default     = true
}

variable "firestore_bot_config_collection" {
  description = "Firestore collection used for storing bot configuration."
  type        = string
  default     = "bot-config"
}

variable "firestore_output_format_doc" {
  description = "Firestore document ID used for custom output formatting instructions."
  type        = string
  default     = "output-format"
}

variable "output_format_instructions" {
  description = "Optional custom formatting instructions applied to every bot response (seeds the Firestore document on first read)."
  type        = string
  default     = ""
}

# ── RS Alerts (Background GTI Alerts -> Teams) ───────────────────────────────

variable "enable_rs_alerts" {
  description = "Set to true to provision the RS Alerts background function and Cloud Scheduler trigger."
  type        = bool
  default     = false
}

variable "rs_alerts_teams_channel_id" {
  description = "Teams channel link or ID (19:...@thread.tacv2) that RS Alerts posts GTI alerts into. Pass the FULL channel link (not a bare ID) so the bot's Teams app can be auto-installed into the team via Microsoft Graph. Required when enable_rs_alerts is true."
  type        = string
  default     = ""
}

variable "rsa_gti_project" {
  description = "Google Threat Intelligence project ID that RS Alerts queries for alerts. This is a GTI-side project, not necessarily the same as `project_id` (the GCP project hosting this infrastructure) — required when enable_rs_alerts is true."
  type        = string
  default     = ""
}

# Optional RSA tuning (defaults shown)
variable "rsa_function_name" {
  description = "Name of the RS Alerts Cloud Run function."
  type        = string
  default     = "gti-alerts-fetch"
}

variable "rsa_schedule" {
  description = "Cron expression for the Cloud Scheduler job triggering RS Alerts. Default: every 15 minutes (matches the Azure deployment's default cadence)."
  type        = string
  default     = "*/15 * * * *"
}

variable "rsa_schedule_timezone" {
  description = "Timezone for the Cloud Scheduler job."
  type        = string
  default     = "Etc/UTC"
}

variable "rsa_backfill_days" {
  description = "Days of GTI alert history to backfill on RS Alerts' first run (1-7)."
  type        = number
  default     = 7
}

variable "rsa_page_size" {
  description = "Page size for GTI Alerts API pagination."
  type        = number
  default     = 1000
}

variable "rsa_function_memory" {
  description = "Memory allocated to the RS Alerts Cloud Run function."
  type        = string
  default     = "256Mi"
}

variable "rsa_function_timeout_seconds" {
  description = "Execution timeout in seconds for the RS Alerts Cloud Run function."
  type        = number
  default     = 540
}

# Optional RSA alert filters
variable "rsa_filter_severity_level" {
  description = "Allowed alert severities (comma-separated: LOW,MEDIUM,HIGH)."
  type        = string
  default     = "MEDIUM,HIGH"
}

variable "rsa_filter_priority_level" {
  description = "Allowed alert priorities (comma-separated: LOW,MEDIUM,HIGH,CRITICAL)."
  type        = string
  default     = "MEDIUM,HIGH,CRITICAL"
}

variable "rsa_filter_relevance_level" {
  description = "Allowed alert relevance levels (comma-separated: LOW,MEDIUM,HIGH)."
  type        = string
  default     = "MEDIUM,HIGH"
}

variable "rsa_filter_relevance_confidence" {
  description = "Allowed alert relevance confidences (comma-separated: LOW,MEDIUM,HIGH)."
  type        = string
  default     = "MEDIUM,HIGH"
}

variable "firestore_state_collection" {
  description = "Firestore collection used for persisting RS Alerts cursor state."
  type        = string
  default     = "rs-alerts-state"
}

variable "firestore_state_doc" {
  description = "Firestore document used for persisting RS Alerts cursor state."
  type        = string
  default     = "cursor"
}

variable "labels" {
  description = "Labels applied to all provisioned GCP resources."
  type        = map(string)
  default = {
    managed-by = "terraform"
    app        = "gti-team-bot-agentic"
  }
}
