# =============================================================================
# GTI Teams Bot (Agentic) — GCP Terraform Infrastructure
# =============================================================================
# Provisions:
#   - Google Project APIs enablement
#   - Google Cloud Storage (GCS) Bucket for source code and Teams manifest zip
#   - Google Cloud Firestore Database (Native Mode) for config & state
#   - Google Secret Manager for GTI_API_KEY and CLIENT_SECRET
#   - Google Cloud Run functions (2nd Gen) for gti-bot (HTTP Webhook)
#   - Google Cloud Run functions (2nd Gen) for rs-alerts (Scheduled Worker)
#   - Google Cloud Scheduler job with OIDC service account authentication
#   - Least-privilege IAM roles and service accounts
#   - Dynamic Teams App Manifest generation, zipping, and GCS upload
# =============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.40"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "azurerm" {
  features {}
}

provider "azuread" {}

# -----------------------------------------------------------------------------
# Microsoft Entra ID (Azure AD) App Registration & Credentials
# -----------------------------------------------------------------------------
data "azurerm_client_config" "current" {}
data "azuread_client_config" "current" {}

resource "azuread_application" "bot_app" {
  display_name = var.bot_name
  owners       = [data.azuread_client_config.current.object_id]
}

resource "azuread_service_principal" "bot_sp" {
  client_id = azuread_application.bot_app.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

resource "azuread_application_password" "bot_secret" {
  application_id = azuread_application.bot_app.id
}

# -----------------------------------------------------------------------------
# Random Suffix for Globally Unique Resources
# -----------------------------------------------------------------------------
resource "random_id" "suffix" {
  byte_length = 4
}

# -----------------------------------------------------------------------------
# Enable Required GCP Services / APIs
# -----------------------------------------------------------------------------
locals {
  required_apis = [
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "storage.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
  ]

  storage_bucket_name = var.storage_bucket_name != "" ? var.storage_bucket_name : "${var.project_id}-${var.bot_name}-storage-${random_id.suffix.hex}"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# Google Cloud Storage (GCS) Bucket for Source Code and Manifests
# -----------------------------------------------------------------------------
resource "google_storage_bucket" "source_bucket" {
  name                        = local.storage_bucket_name
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  labels = var.labels

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Package & Upload gti-bot Source Code
# -----------------------------------------------------------------------------
data "archive_file" "gti_bot_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../gti-bot"
  output_path = "${path.module}/.build/gti-bot.zip"
  excludes = [
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".env",
    ".env.example",
    ".gitignore",
    "README.md",
    "teams-app-manifest",
    "GTI Slack AI Integration TDD.md",
    "GTI Teams AI Integration TDD.md",
  ]
}

resource "google_storage_bucket_object" "gti_bot_source" {
  name   = "source/gti-bot-${data.archive_file.gti_bot_zip.output_md5}.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.gti_bot_zip.output_path
}

# -----------------------------------------------------------------------------
# Package & Upload rs-alerts Source Code (if enabled)
# -----------------------------------------------------------------------------
data "archive_file" "rs_alerts_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../rs-alerts"
  output_path = "${path.module}/.build/rs-alerts.zip"
  excludes = [
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".env",
    ".env.example",
    ".gitignore",
    "README.md",
  ]
}

resource "google_storage_bucket_object" "rs_alerts_source" {
  count  = var.enable_rs_alerts ? 1 : 0
  name   = "source/rs-alerts-${data.archive_file.rs_alerts_zip.output_md5}.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.rs_alerts_zip.output_path
}

# -----------------------------------------------------------------------------
# Teams App Manifest Package (Render, Zip & Upload to GCS)
# -----------------------------------------------------------------------------
resource "local_file" "manifest_json" {
  content = jsonencode({
    "$schema"         = "https://developer.microsoft.com/en-us/json-schemas/teams/v1.25/MicrosoftTeams.schema.json"
    "manifestVersion" = "1.25"
    "version"         = "1.0.1"
    "id"              = azuread_application.bot_app.client_id
    "developer" = {
      "name"          = "Google Threat Intelligence"
      "websiteUrl"    = "https://www.virustotal.com"
      "privacyUrl"    = "https://www.virustotal.com/gui/privacy-policy"
      "termsOfUseUrl" = "https://www.virustotal.com/gui/terms-of-service"
    }
    "icons" = {
      "color"   = "color.png"
      "outline" = "outline.png"
    }
    "name" = {
      "short" = "GTI Agent"
      "full"  = "Google Threat Intelligence Agent"
    }
    "description" = {
      "short" = "Google Threat Intelligence Agentic Bot for Microsoft Teams."
      "full"  = "Google Threat Intelligence Agentic Bot for Microsoft Teams. Ask threat intelligence questions directly to perform indicator lookups, malware analysis, reputation checks, and threat actor research."
    }
    "accentColor" = "#2D1B4D"
    "bots" = [
      {
        "botId"              = azuread_application.bot_app.client_id
        "scopes"             = ["personal", "team", "groupChat"]
        "supportsFiles"      = false
        "isNotificationOnly" = false
      }
    ]
    "permissions"             = ["identity", "messageTeamMembers"]
    "validDomains"            = []
    "supportsChannelFeatures" = "tier1"
  })
  filename = "${path.module}/.build/manifest_pkg/manifest.json"
}

resource "local_file" "manifest_color_png" {
  content_base64 = filebase64("${path.module}/../gti-bot/teams-app-manifest/color.png")
  filename       = "${path.module}/.build/manifest_pkg/color.png"
}

resource "local_file" "manifest_outline_png" {
  content_base64 = filebase64("${path.module}/../gti-bot/teams-app-manifest/outline.png")
  filename       = "${path.module}/.build/manifest_pkg/outline.png"
}

data "archive_file" "teams_manifest_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/manifest_pkg"
  output_path = "${path.module}/.build/teams-app-manifest.zip"

  depends_on = [
    local_file.manifest_json,
    local_file.manifest_color_png,
    local_file.manifest_outline_png,
  ]
}

resource "google_storage_bucket_object" "teams_manifest_zip" {
  name   = "teams-manifest/teams-app-manifest.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.teams_manifest_zip.output_path
}

# -----------------------------------------------------------------------------
# Google Cloud Firestore Database (Native Mode)
# -----------------------------------------------------------------------------
resource "google_firestore_database" "database" {
  count                       = var.create_firestore_database ? 1 : 0
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  deletion_policy             = "DELETE"

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Google Secret Manager (GTI API Key & Client Secret)
# -----------------------------------------------------------------------------
resource "google_secret_manager_secret" "gti_api_key" {
  secret_id = "${var.bot_name}-gti-api-key"
  labels    = var.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "gti_api_key_version" {
  secret      = google_secret_manager_secret.gti_api_key.id
  secret_data = var.gti_api_key
}

resource "google_secret_manager_secret" "client_secret" {
  secret_id = "${var.bot_name}-client-secret"
  labels    = var.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "client_secret_version" {
  secret      = google_secret_manager_secret.client_secret.id
  secret_data = azuread_application_password.bot_secret.value
}

# -----------------------------------------------------------------------------
# Service Accounts & IAM Roles
# -----------------------------------------------------------------------------

# 1. Service Account for gti-bot
resource "google_service_account" "bot_sa" {
  account_id   = "${var.bot_name}-sa"
  display_name = "Service Account for ${var.bot_name} Cloud Run function"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "bot_sa_datastore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.bot_sa.email}"
}

resource "google_project_iam_member" "bot_sa_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.bot_sa.email}"
}

resource "google_project_iam_member" "bot_sa_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.bot_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "bot_gti_key_accessor" {
  secret_id = google_secret_manager_secret.gti_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.bot_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "bot_client_secret_accessor" {
  secret_id = google_secret_manager_secret.client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.bot_sa.email}"
}

# 2. Service Account for rs-alerts (if enabled)
resource "google_service_account" "rs_alerts_sa" {
  count        = var.enable_rs_alerts ? 1 : 0
  account_id   = "${var.bot_name}-rs-sa"
  display_name = "Service Account for ${var.bot_name} RS Alerts"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "rs_alerts_sa_datastore" {
  count   = var.enable_rs_alerts ? 1 : 0
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.rs_alerts_sa[0].email}"
}

resource "google_project_iam_member" "rs_alerts_sa_log_writer" {
  count   = var.enable_rs_alerts ? 1 : 0
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.rs_alerts_sa[0].email}"
}

resource "google_secret_manager_secret_iam_member" "rs_alerts_gti_key_accessor" {
  count     = var.enable_rs_alerts ? 1 : 0
  secret_id = google_secret_manager_secret.gti_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.rs_alerts_sa[0].email}"
}

resource "google_secret_manager_secret_iam_member" "rs_alerts_client_secret_accessor" {
  count     = var.enable_rs_alerts ? 1 : 0
  secret_id = google_secret_manager_secret.client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.rs_alerts_sa[0].email}"
}

# 3. Service Account for Cloud Scheduler (if enabled)
resource "google_service_account" "scheduler_sa" {
  count        = var.enable_rs_alerts ? 1 : 0
  account_id   = "${var.bot_name}-sched-sa"
  display_name = "Service Account for Cloud Scheduler triggering RS Alerts"
  depends_on   = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Cloud Run function (2nd Gen) — gti-bot
# -----------------------------------------------------------------------------
resource "google_cloudfunctions2_function" "gti_bot" {
  name        = var.bot_name
  location    = var.region
  description = "Google Threat Intelligence Teams Agentic Bot"
  labels      = var.labels

  build_config {
    runtime     = var.python_runtime
    entry_point = "gti_bot_http"
    source {
      storage_source {
        bucket = google_storage_bucket.source_bucket.name
        object = google_storage_bucket_object.gti_bot_source.name
      }
    }
  }

  service_config {
    max_instance_count             = var.max_instances
    min_instance_count             = var.min_instances
    available_memory               = var.memory
    timeout_seconds                = var.timeout_seconds
    service_account_email          = google_service_account.bot_sa.email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    environment_variables = {
      GCP_PROJECT_ID                  = var.project_id
      CLIENT_ID                       = azuread_application.bot_app.client_id
      TENANT_ID                       = data.azuread_client_config.current.tenant_id
      GTI_API_BASE_URL                = var.gti_api_base_url
      FIRESTORE_DATABASE              = "(default)"
      FIRESTORE_BOT_CONFIG_COLLECTION = var.firestore_bot_config_collection
      FIRESTORE_OUTPUT_FORMAT_DOC     = var.firestore_output_format_doc
      OUTPUT_FORMAT_INSTRUCTIONS      = var.output_format_instructions
    }

    secret_environment_variables {
      key        = "GTI_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.gti_api_key.secret_id
      version    = "latest"
    }

    secret_environment_variables {
      key        = "CLIENT_SECRET"
      project_id = var.project_id
      secret     = google_secret_manager_secret.client_secret.secret_id
      version    = "latest"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.gti_api_key_version,
    google_secret_manager_secret_version.client_secret_version,
  ]
}

# Public access for gti-bot (Teams Bot Framework verifies signatures in-app via FastAPIAdapter)
resource "google_cloud_run_service_iam_member" "gti_bot_public" {
  location = var.region
  service  = google_cloudfunctions2_function.gti_bot.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# Cloud Run function (2nd Gen) — rs-alerts (if enabled)
# -----------------------------------------------------------------------------
resource "google_cloudfunctions2_function" "rs_alerts" {
  count       = var.enable_rs_alerts ? 1 : 0
  name        = var.rsa_function_name
  location    = var.region
  description = "GTI Threat Intelligence Alerts to Microsoft Teams Background Worker"
  labels      = var.labels

  build_config {
    runtime     = var.python_runtime
    entry_point = "rs_alerts_http"
    source {
      storage_source {
        bucket = google_storage_bucket.source_bucket.name
        object = google_storage_bucket_object.rs_alerts_source[0].name
      }
    }
  }

  service_config {
    max_instance_count             = 1
    min_instance_count             = 0
    available_memory               = var.rsa_function_memory
    timeout_seconds                = var.rsa_function_timeout_seconds
    service_account_email          = google_service_account.rs_alerts_sa[0].email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    environment_variables = {
      GCP_PROJECT_ID              = var.project_id
      CLIENT_ID                   = azuread_application.bot_app.client_id
      TENANT_ID                   = data.azuread_client_config.current.tenant_id
      TEAMS_CHANNEL_ID            = var.rs_alerts_teams_channel_id_or_link
      GTI_RSA_PROJECT             = var.rsa_gti_project
      PAGE_SIZE                   = tostring(var.rsa_page_size)
      FILTER_SEVERITY_LEVEL       = var.rsa_filter_severity_level
      FILTER_PRIORITY_LEVEL       = var.rsa_filter_priority_level
      FILTER_RELEVANCE_LEVEL      = var.rsa_filter_relevance_level
      FILTER_RELEVANCE_CONFIDENCE = var.rsa_filter_relevance_confidence
      FIRESTORE_DATABASE          = "(default)"
      FIRESTORE_STATE_COLLECTION  = var.firestore_state_collection
      FIRESTORE_STATE_DOC         = var.firestore_state_doc
    }

    secret_environment_variables {
      key        = "GTI_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.gti_api_key.secret_id
      version    = "latest"
    }

    secret_environment_variables {
      key        = "CLIENT_SECRET"
      project_id = var.project_id
      secret     = google_secret_manager_secret.client_secret.secret_id
      version    = "latest"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.gti_api_key_version,
    google_secret_manager_secret_version.client_secret_version,
  ]
}

# Cloud Scheduler IAM permission to invoke rs-alerts
resource "google_cloud_run_service_iam_member" "rs_alerts_invoker" {
  count    = var.enable_rs_alerts ? 1 : 0
  location = var.region
  service  = google_cloudfunctions2_function.rs_alerts[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa[0].email}"
}

# -----------------------------------------------------------------------------
# Cloud Scheduler Job (if enabled)
# -----------------------------------------------------------------------------
resource "google_cloud_scheduler_job" "rs_alerts_schedule" {
  count            = var.enable_rs_alerts ? 1 : 0
  name             = "${var.rsa_function_name}-schedule"
  description      = "Scheduled trigger for GTI RS Alerts to Teams background job"
  schedule         = var.rsa_schedule
  time_zone        = var.rsa_schedule_timezone
  attempt_deadline = "${var.rsa_function_timeout_seconds}s"

  http_target {
    uri         = google_cloudfunctions2_function.rs_alerts[0].service_config[0].uri
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.scheduler_sa[0].email
      audience              = google_cloudfunctions2_function.rs_alerts[0].service_config[0].uri
    }
  }

  depends_on = [
    google_project_service.apis,
    google_cloudfunctions2_function.rs_alerts,
  ]
}

# -----------------------------------------------------------------------------
# Azure Bot Service & Teams Channel
# -----------------------------------------------------------------------------
resource "azurerm_resource_group" "bot_rg" {
  name     = "${var.bot_name}-rg"
  location = "eastus"
  tags     = var.labels
}

resource "azurerm_bot_service_azure_bot" "bot" {
  name                = var.bot_name
  resource_group_name = azurerm_resource_group.bot_rg.name
  location            = "global"
  sku                 = "F0"
  microsoft_app_id    = azuread_application.bot_app.client_id
  endpoint            = "${google_cloudfunctions2_function.gti_bot.service_config[0].uri}/api/messages"
  tags                = var.labels
}

resource "azurerm_bot_channel_ms_teams" "teams" {
  bot_name            = azurerm_bot_service_azure_bot.bot.name
  location            = azurerm_bot_service_azure_bot.bot.location
  resource_group_name = azurerm_resource_group.bot_rg.name
}

