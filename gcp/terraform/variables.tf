# =============================================================================
# GTI Teams Bot (Agentic) — GCP Terraform Variables
# =============================================================================

variable "project_id" {
  description = "Google Cloud Project ID where all resources will be created."
  type        = string
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

variable "storage_bucket_name" {
  description = "Globally-unique GCS bucket name for source code and manifests. Empty = derive from project_id/bot_name plus a random suffix."
  type        = string
  default     = ""
}

variable "azure_bot_name" {
  description = "Globally-unique name for the Azure Bot Service resource (Bot Service names are unique across all of Azure, not just this subscription). Empty = derive from bot_name plus a random suffix."
  type        = string
  default     = ""
}

variable "azure_resource_group_name" {
  description = "Name of an EXISTING Azure resource group to deploy the Bot Service into. Leave empty to create a new resource group named '<bot_name>-rg' — creating a resource group requires subscription-level permission, which the deploying identity may not have if its access is scoped to a single existing resource group."
  type        = string
  default     = ""
}

variable "azure_region" {
  description = "Azure region for a newly-created resource group (ignored when azure_resource_group_name points at an existing one)."
  type        = string
  default     = "eastus"
}


variable "runtime" {
  description = "Cloud Functions runtime for the gti-bot function."
  type        = string
  default     = "python312"
}

variable "cpu" {
  description = "CPU allocated to the gti-bot Cloud Run function (e.g. 1, 2, 4)."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory allocated to the gti-bot Cloud Run function (e.g. 512Mi, 1Gi, 2Gi)."
  type        = string
  default     = "2Gi"
}

variable "timeout_seconds" {
  description = "Execution timeout in seconds for the gti-bot function (max 3600 for HTTP)."
  type        = number
  default     = 1800
  validation {
    condition     = var.timeout_seconds >= 1 && var.timeout_seconds <= 3600
    error_message = "timeout_seconds must be between 1 and 3600 (Cloud Functions Gen2 HTTP function limit)."
  }
}

variable "max_instances" {
  description = "Maximum scale-out instance count for the gti-bot function."
  type        = number
  default     = 5
  validation {
    condition     = var.max_instances >= 1
    error_message = "max_instances must be at least 1."
  }
}

variable "concurrency" {
  description = "Maximum concurrent requests per instance for the gti-bot function. Also sets the THREADS env var (see main.tf) to match."
  type        = number
  default     = 40
  validation {
    condition     = var.concurrency >= 1 && var.concurrency <= 1000
    error_message = "concurrency must be between 1 and 1000 (Cloud Run's max_instance_request_concurrency limit)."
  }
}

variable "min_instances" {
  description = "Minimum idle instance count for warm starts (0 allows scaling to zero)."
  type        = number
  default     = 0
  validation {
    condition     = var.min_instances >= 0
    error_message = "min_instances must be 0 or greater."
  }
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

variable "firestore_database" {
  description = "Firestore database id shared by the gti-bot and rs-alerts functions. Use a custom name (e.g. the default below) if the '(default)' database already exists / is used by another app in this GCP project; set to \"(default)\" explicitly to use the project's default database instead."
  type        = string
  default     = "gti-team-bot-db"
}

variable "create_firestore_database" {
  description = "Whether Terraform should provision the Firestore database named by firestore_database. Set to false to reuse an existing database instead (e.g. one already created outside Terraform, or the project's pre-existing '(default)' database)."
  type        = bool
  default     = true
}

variable "firestore_deletion_policy" {
  description = "Deletion policy for the Firestore database on 'terraform destroy' ('DELETE' or 'ABANDON'). 'ABANDON' leaves the database (and its data) intact instead of deleting it."
  type        = string
  default     = "DELETE"
  validation {
    condition     = contains(["DELETE", "ABANDON"], var.firestore_deletion_policy)
    error_message = "firestore_deletion_policy must be either \"DELETE\" or \"ABANDON\"."
  }
}

variable "firestore_bot_config_collection" {
  description = "Firestore collection storing the bot's output-format config and per-thread GTI session ids."
  type        = string
  default     = "gti-bot-config"
}

variable "firestore_output_format_doc" {
  description = "Firestore document (within firestore_bot_config_collection) storing the custom output-format instructions."
  type        = string
  default     = "gti-custom-output-format"
}

variable "output_format_instructions" {
  description = "Optional custom formatting instructions applied to every bot response (seeds the Firestore document on first read)."
  type        = string
  default     = ""
}

# ── Microsoft Graph (channel thread context) ─────────────────────────────────
# Requires the bot's Entra app registration to be granted the Graph
# APPLICATION permission ChannelMessage.Read.All with tenant-admin consent —
# separate from the Bot Framework permissions it already has.

variable "thread_context_enabled" {
  description = "Whether to fetch and inject channel-thread message history as context for each query."
  type        = bool
  default     = true
}

variable "thread_context_message_count" {
  description = "Number of most-recent channel-thread messages to fetch as context for each query."
  type        = number
  default     = 5
}

# ── RS Alerts (Background GTI Alerts -> Teams) ───────────────────────────────

variable "enable_rs_alerts" {
  description = "Set to false to skip provisioning RS Alerts: a background, scheduled Cloud Run function that posts new Google Threat Intelligence alerts to a Teams channel. Enabled by default — rs_alerts_teams_channel_id_or_link and rsa_gti_project must be set for it to actually run (enforced by this module's preconditions at plan/apply time)."
  type        = bool
  default     = true
}

variable "rs_alerts_teams_channel_id_or_link" {
  description = "Teams channel link or bare ID (19:...@thread.tacv2) that RS Alerts posts GTI alerts into — either form works. The bot's Teams app must already be added to the target team manually beforehand. Required when enable_rs_alerts is true."
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

variable "rsa_runtime" {
  description = "Cloud Functions runtime for the RS Alerts function."
  type        = string
  default     = "python312"
}

variable "rsa_schedule" {
  description = "Cron expression for the Cloud Scheduler job triggering RS Alerts. Default: hourly, at minute 0."
  type        = string
  default     = "0 * * * *"
}

variable "rsa_schedule_timezone" {
  description = "Timezone for the Cloud Scheduler job."
  type        = string
  default     = "Etc/UTC"
}

variable "rsa_page_size" {
  description = "Page size for GTI Alerts API pagination."
  type        = number
  default     = 1000
}

variable "rsa_backfill_days" {
  description = "Backfill window (days, 1-7) used on the very first run when no Firestore cursor exists yet."
  type        = number
  default     = 7
  validation {
    condition     = var.rsa_backfill_days >= 1 && var.rsa_backfill_days <= 7
    error_message = "rsa_backfill_days must be between 1 and 7 — values outside this range are silently clamped to 7 by the application at runtime."
  }
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

# Alert filters — comma-separated values, AND'd together (OR'd within each
# field). Matches app/gti_client.py's _LEVEL_FILTERS defaults. Widen a field
# to every valid value (e.g. "LOW,MEDIUM,HIGH") to disable filtering on it —
# never leave it empty, that fails every RS Alerts run instead.
variable "rsa_filter_severity_level" {
  description = "severity_analysis.severity_level values to include. Valid: LOW, MEDIUM, HIGH"
  type        = string
  default     = "MEDIUM,HIGH"
}

variable "rsa_filter_priority_level" {
  description = "priority_analysis.priority_level values to include. Valid: LOW, MEDIUM, HIGH, CRITICAL"
  type        = string
  default     = "MEDIUM,HIGH,CRITICAL"
}

variable "rsa_filter_relevance_level" {
  description = "relevance_analysis.relevance_level values to include. Valid: LOW, MEDIUM, HIGH"
  type        = string
  default     = "MEDIUM,HIGH"
}

variable "rsa_filter_relevance_confidence" {
  description = "relevance_analysis.confidence values to include. Valid: LOW, MEDIUM, HIGH"
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
