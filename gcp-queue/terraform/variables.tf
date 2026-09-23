# =============================================================================
# GTI Teams Bot (Agentic) — GCP Queue-Decoupled Terraform Variables
# =============================================================================

variable "project_id" {
  description = "Google Cloud Project ID where all resources will be created."
  type        = string
}

variable "region" {
  description = "Google Cloud region for all resources (Cloud Functions, GCS, Firestore, Pub/Sub, Secrets)."
  type        = string
  default     = "us-central1"
}

# Defaults to a name distinct from gcp/terraform's "gti-team-bot" — every
# resource below derives its name from this (service accounts, secrets,
# Pub/Sub topics), and service-account/secret IDs are project-unique. Reusing
# the same bot_name for both stacks in the same GCP project WILL collide.
variable "bot_name" {
  description = "Base resource name for the bot application. Keep distinct from gcp/terraform's bot_name if deploying both stacks into the same project — service account and Secret Manager IDs are project-unique."
  type        = string
  default     = "gti-team-bot-queue"
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

# ── Ingest Cloud Function tuning ─────────────────────────────────────────────

variable "ingest_runtime" {
  description = "Cloud Functions runtime for bot-ingest-function."
  type        = string
  default     = "python312"
}

variable "ingest_cpu" {
  description = "CPU allocated to the ingest Cloud Run function."
  type        = string
  default     = "1"
}

variable "ingest_memory" {
  description = "Memory allocated to the ingest Cloud Run function — small, since it never calls GTI/Graph, just verifies a JWT and publishes a job."
  type        = string
  default     = "512Mi"
}

variable "ingest_timeout_seconds" {
  description = "Execution timeout in seconds for the ingest function. Generous headroom is harmless here — every real request finishes in well under a second (see the README's timeout-budget section for why the WORKER's timeout is the one that actually matters)."
  type        = number
  default     = 60
}

variable "ingest_max_instances" {
  description = "Maximum scale-out instance count for the ingest function."
  type        = number
  default     = 10
  validation {
    condition     = var.ingest_max_instances >= 1
    error_message = "ingest_max_instances must be at least 1."
  }
}

variable "ingest_min_instances" {
  description = "Minimum idle instance count for the ingest function (0 allows scaling to zero)."
  type        = number
  default     = 0
}

variable "ingest_concurrency" {
  description = "Maximum concurrent requests per ingest instance. Also sets the THREADS env var (see main.tf) to match."
  type        = number
  default     = 20
  validation {
    condition     = var.ingest_concurrency >= 1 && var.ingest_concurrency <= 1000
    error_message = "ingest_concurrency must be between 1 and 1000 (Cloud Run's max_instance_request_concurrency limit)."
  }
}

# ── Worker Cloud Function tuning ─────────────────────────────────────────────

variable "worker_runtime" {
  description = "Cloud Functions runtime for bot-worker-function."
  type        = string
  default     = "python312"
}

variable "worker_cpu" {
  description = "CPU allocated to the worker Cloud Run function."
  type        = string
  default     = "1"
}

variable "worker_memory" {
  description = "Memory allocated to the worker Cloud Run function."
  type        = string
  default     = "2Gi"
}

variable "worker_timeout_seconds" {
  description = "Execution timeout in seconds for the worker function. Deliberately capped at 600 (not the Cloud Functions Gen2 HTTP ceiling of 3600) — the job_subscription's ack_deadline_seconds is hardcoded to Pub/Sub's own hard maximum of 600s in main.tf (a push subscription has no lease-renewal mechanism, unlike Azure Storage Queue's automatic peek-lock, so this really is a fixed platform ceiling, not a quota). Nothing beyond 600s is ever reachable — Pub/Sub will have already redelivered the message to a second instance by then."
  type        = number
  default     = 600
  validation {
    condition     = var.worker_timeout_seconds >= 1 && var.worker_timeout_seconds <= 600
    error_message = "worker_timeout_seconds must be between 1 and 600 — see this variable's description for why 600 is a hard ceiling here, not just this module's default."
  }
}

variable "worker_max_instances" {
  description = "Maximum scale-out instance count for the worker function."
  type        = number
  default     = 10
  validation {
    condition     = var.worker_max_instances >= 1
    error_message = "worker_max_instances must be at least 1."
  }
}

variable "worker_min_instances" {
  description = "Minimum idle instance count for the worker function. Defaults to 1 (not 0, unlike the ingest function) — cold start competes directly against the fixed 600s ack_deadline_seconds budget in a way it never did for the old synchronous gti-bot's much more generous timeout."
  type        = number
  default     = 1
  validation {
    condition     = var.worker_min_instances >= 0
    error_message = "worker_min_instances must be 0 or greater."
  }
}

variable "worker_concurrency" {
  description = "Maximum concurrent requests per worker instance. Kept low relative to the old synchronous gti-bot's default — each held request now occupies the instance for potentially minutes, so a high value just means more slow requests piling onto one instance's CPU/memory instead of Cloud Run scaling out to more instances. Also sets the THREADS env var (see main.tf) to match."
  type        = number
  default     = 15
  validation {
    condition     = var.worker_concurrency >= 1 && var.worker_concurrency <= 1000
    error_message = "worker_concurrency must be between 1 and 1000 (Cloud Run's max_instance_request_concurrency limit)."
  }
}

# ── Pub/Sub job hand-off ─────────────────────────────────────────────────────

variable "max_delivery_attempts" {
  description = "How many times Pub/Sub redelivers a job to the worker before routing it to the dead-letter topic. 5 is Pub/Sub's own platform floor — there is no way to go lower (Azure's equivalent, maxDequeueCount, was 2). See the README's platform-differences section."
  type        = number
  default     = 5
  validation {
    condition     = var.max_delivery_attempts >= 5 && var.max_delivery_attempts <= 100
    error_message = "max_delivery_attempts must be between 5 and 100 (Pub/Sub dead_letter_policy's own allowed range)."
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
  description = "Firestore database id used by this stack. Defaults to a name distinct from gcp/terraform's own firestore_database so the two stacks' session/config data never collide if deployed into the same GCP project (Firestore supports multiple named databases per project)."
  type        = string
  default     = "gti-team-bot-queue-db"
}

variable "create_firestore_database" {
  description = "Whether Terraform should provision the Firestore database named by firestore_database. Set to false to reuse an existing database instead."
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
  description = "Firestore collection storing the bot's output-format config, per-thread GTI session ids, and redelivery dedup claims."
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

variable "labels" {
  description = "Labels applied to all provisioned GCP resources."
  type        = map(string)
  default = {
    managed-by = "terraform"
    app        = "gti-team-bot-agentic-queue"
  }
}
