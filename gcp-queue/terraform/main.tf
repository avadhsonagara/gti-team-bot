# =============================================================================
# GTI Teams Bot (Agentic) — GCP Queue-Decoupled Terraform Infrastructure
# =============================================================================
# Provisions a standalone, independently-deployable counterpart to
# gcp/terraform: two Cloud Run functions (2nd Gen) — bot-ingest-function
# (public HTTP webhook) and bot-worker-function (Pub/Sub push target only) —
# decoupled by a Pub/Sub topic, so a GTI query that takes 5-10 minutes never
# has to answer Bot Framework's 15-second response deadline synchronously.
# See ../README.md for the full architecture rationale.
#
# Standalone by design (own Azure AD app registration, own Bot Service, own
# Secret Manager secrets, own Firestore database) — mirrors how gcp/terraform
# is already fully self-provisioning, and lets this stack be validated
# side-by-side with gcp/ before any cutover decision. Not additive to
# gcp/terraform: there is no precedent in this repo for one Terraform stack
# consuming another sibling stack's resources.
#
# Provisions:
#   - Google Project APIs enablement (including pubsub.googleapis.com)
#   - Google Cloud Storage (GCS) bucket for source code and Teams manifest zip
#   - Google Cloud Firestore Database (Native Mode) for sessions/config/dedup
#   - Google Secret Manager for GTI_API_KEY and CLIENT_SECRET
#   - Pub/Sub job topic + push subscription (worker), dead-letter topic +
#     push subscription (poison notifications) — see the IAM section below
#     for the three distinct grants this requires
#   - Cloud Run functions (2nd Gen) for bot-ingest-function (public) and
#     bot-worker-function (locked to the Pub/Sub push identity via IAM)
#   - Least-privilege IAM roles and service accounts (three: ingest, worker,
#     and a dedicated Pub/Sub push identity)
#   - Dynamic Teams App Manifest generation, zipping, and GCS upload
#   - Azure Bot Service & Teams channel registration
# =============================================================================

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.38.0"
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
# A second, independent bot identity from gcp/terraform's — Azure allows
# multiple Bot Service registrations per tenant, and this stack is meant to
# be validated side-by-side rather than sharing the original bot's identity.
data "azurerm_client_config" "current" {}
data "azuread_client_config" "current" {}

# Microsoft Graph's own service principal — its application (client) id
# (00000003-0000-0000-c000-000000000000) is a fixed, universal constant
# across every Azure tenant, but its per-tenant service principal OBJECT id
# is not, so it's looked up rather than hardcoded.
data "azuread_service_principal" "msgraph" {
  client_id = "00000003-0000-0000-c000-000000000000"
}

resource "azuread_application" "bot_app" {
  display_name = var.bot_name
  owners       = [data.azuread_client_config.current.object_id]

  # Declares (requests) the three Microsoft Graph APPLICATION permissions
  # the worker needs — app/teams/thread.py (ChannelMessage.Read.All) and
  # app/teams/attachments.py (Chat.Read.All, Files.ReadWrite.All; see
  # README.md's "Requirements for both functions" for exactly why each is
  # needed). Declaring this here, rather than granting it out-of-band via
  # `az ad app permission add`, is what stops Terraform from treating a
  # manually-added grant as drift to be reverted on the next unrelated
  # `apply` — it very nearly did exactly that. The role ids below were
  # looked up from Microsoft Graph's own appRoles (not guessed):
  # `az ad sp show --id 00000003-0000-0000-c000-000000000000 --query "appRoles[?value=='<name>'].id"`.
  required_resource_access {
    resource_app_id = data.azuread_service_principal.msgraph.client_id

    resource_access {
      id   = "7b2449af-6ccd-4f4d-9f78-e550c193f0d1" # ChannelMessage.Read.All
      type = "Role"
    }
    resource_access {
      id   = "6b7d71aa-70aa-4810-a8d9-5d9fb2830017" # Chat.Read.All
      type = "Role"
    }
    resource_access {
      id   = "75359482-378d-4052-8f01-80520e7db3cd" # Files.ReadWrite.All
      type = "Role"
    }
  }
}

resource "azuread_service_principal" "bot_sp" {
  client_id = azuread_application.bot_app.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

resource "azuread_application_password" "bot_secret" {
  application_id = azuread_application.bot_app.id
}

# Tenant-admin consent for the three declared permissions above —
# `required_resource_access` alone only REQUESTS them (equivalent to the
# "API permissions" list in the Portal); this is the actual grant
# (equivalent to clicking "Grant admin consent for <tenant>"), without which
# the worker's Graph calls would fail with 403s despite the permissions
# appearing "added." Requires the applying identity to hold sufficient Entra
# ID rights (Global Administrator / Privileged Role Administrator /
# Application Administrator) — a plain Azure subscription Owner/Contributor
# role is a separate, insufficient permission system.
resource "azuread_app_role_assignment" "worker_channel_message_read_all" {
  app_role_id         = "7b2449af-6ccd-4f4d-9f78-e550c193f0d1" # ChannelMessage.Read.All
  principal_object_id = azuread_service_principal.bot_sp.object_id
  resource_object_id  = data.azuread_service_principal.msgraph.object_id
}

resource "azuread_app_role_assignment" "worker_chat_read_all" {
  app_role_id         = "6b7d71aa-70aa-4810-a8d9-5d9fb2830017" # Chat.Read.All
  principal_object_id = azuread_service_principal.bot_sp.object_id
  resource_object_id  = data.azuread_service_principal.msgraph.object_id
}

resource "azuread_app_role_assignment" "worker_files_readwrite_all" {
  app_role_id         = "75359482-378d-4052-8f01-80520e7db3cd" # Files.ReadWrite.All
  principal_object_id = azuread_service_principal.bot_sp.object_id
  resource_object_id  = data.azuread_service_principal.msgraph.object_id
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
    "pubsub.googleapis.com",
    "storage.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
    "iam.googleapis.com",
  ]

  storage_bucket_name = var.storage_bucket_name != "" ? var.storage_bucket_name : "${var.project_id}-${var.bot_name}-storage-${random_id.suffix.hex}"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# The Pub/Sub service agent (service-{project_number}@gcp-sa-pubsub.iam.gserviceaccount.com)
# is what actually performs dead-letter forwarding and signs OIDC push
# tokens — it materializes once pubsub.googleapis.com is enabled, not before.
# Built via data.google_project (not google_project_service_identity, which
# needs the google-beta provider this repo doesn't otherwise use) — every
# IAM grant below that references it carries an explicit depends_on to avoid
# a "doesn't exist yet" race against API enablement.
data "google_project" "project" {
  project_id = var.project_id
}

locals {
  pubsub_service_agent_email = "service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
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
# Package & Upload bot-ingest-function Source Code
# -----------------------------------------------------------------------------
data "archive_file" "ingest_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../bot-ingest-function"
  output_path = "${path.module}/.build/bot-ingest-function.zip"
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

resource "google_storage_bucket_object" "ingest_source" {
  name   = "source/bot-ingest-function-${data.archive_file.ingest_zip.output_md5}.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.ingest_zip.output_path
}

# -----------------------------------------------------------------------------
# Package & Upload bot-worker-function Source Code
# -----------------------------------------------------------------------------
data "archive_file" "worker_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../bot-worker-function"
  output_path = "${path.module}/.build/bot-worker-function.zip"
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

resource "google_storage_bucket_object" "worker_source" {
  name   = "source/bot-worker-function-${data.archive_file.worker_zip.output_md5}.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.worker_zip.output_path
}

# -----------------------------------------------------------------------------
# Teams App Manifest Package (Render, Zip & Upload to GCS)
# -----------------------------------------------------------------------------
resource "local_file" "manifest_json" {
  content = jsonencode({
    "$schema"         = "https://developer.microsoft.com/en-us/json-schemas/teams/v1.30/MicrosoftTeams.schema.json"
    "manifestVersion" = "1.30"
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
      "short" = "GTI Agent (Queue)"
      "full"  = "Google Threat Intelligence Agent — Queue-Decoupled"
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
        "supportsFiles"      = true
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
  content_base64 = filebase64("${path.module}/../teams-app-manifest/color.png")
  filename       = "${path.module}/.build/manifest_pkg/color.png"
}

resource "local_file" "manifest_outline_png" {
  content_base64 = filebase64("${path.module}/../teams-app-manifest/outline.png")
  filename       = "${path.module}/.build/manifest_pkg/outline.png"
}

data "archive_file" "teams_manifest_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/manifest_pkg"
  output_path = "${path.module}/.build/gti-teams-bot-queue-manifest.zip"

  depends_on = [
    local_file.manifest_json,
    local_file.manifest_color_png,
    local_file.manifest_outline_png,
  ]
}

resource "google_storage_bucket_object" "teams_manifest_zip" {
  name   = "gti-teams-bot-queue/gti-teams-bot-queue-manifest.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.teams_manifest_zip.output_path
}

# -----------------------------------------------------------------------------
# Google Cloud Firestore Database (Native Mode)
# -----------------------------------------------------------------------------
resource "google_firestore_database" "database" {
  count                       = var.create_firestore_database ? 1 : 0
  project                     = var.project_id
  name                        = var.firestore_database
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  deletion_policy             = var.firestore_deletion_policy

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
# Three single-purpose identities, deliberately kept separate:
#   ingest_sa            — the ingest function's own runtime identity.
#   worker_sa             — the worker function's own runtime identity.
#   pubsub_push_identity  — NOT a runtime identity for either function; the
#                           OIDC identity Pub/Sub itself authenticates AS when
#                           pushing to the worker. Only this identity is
#                           granted roles/run.invoker on the worker.

resource "google_service_account" "ingest_sa" {
  account_id   = "${var.bot_name}-ingest-sa"
  display_name = "Service Account for ${var.bot_name} ingest Cloud Run function"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "worker_sa" {
  account_id   = "${var.bot_name}-worker-sa"
  display_name = "Service Account for ${var.bot_name} worker Cloud Run function"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "pubsub_push_identity" {
  account_id   = "${var.bot_name}-push-sa"
  display_name = "OIDC identity Pub/Sub authenticates as when pushing jobs to the worker"
  depends_on   = [google_project_service.apis]
}

# ── ingest_sa: publish to the job topic, post the placeholder (client_secret) ─
resource "google_project_iam_member" "ingest_sa_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ingest_sa.email}"
}

resource "google_project_iam_member" "ingest_sa_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.ingest_sa.email}"
}

# NOT granted gti_api_key or datastore.user — ingest never calls GTI or
# touches Firestore, only client_secret (to post the placeholder / error
# notices via the Bot Framework Connector — see app/teams/bot_client.py).
resource "google_secret_manager_secret_iam_member" "ingest_client_secret_accessor" {
  secret_id = google_secret_manager_secret.client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ingest_sa.email}"
}

# ── worker_sa: both secrets + Firestore (sessions, output format, dedup) ──────
resource "google_project_iam_member" "worker_sa_datastore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_project_iam_member" "worker_sa_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_project_iam_member" "worker_sa_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_gti_key_accessor" {
  secret_id = google_secret_manager_secret.gti_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_client_secret_accessor" {
  secret_id = google_secret_manager_secret.client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_sa.email}"
}

# -----------------------------------------------------------------------------
# bot-ingest-function — Cloud Run function (2nd Gen), built from source
# -----------------------------------------------------------------------------
resource "google_cloudfunctions2_function" "ingest" {
  name        = "${var.bot_name}-ingest"
  location    = var.region
  description = "GTI Teams Bot — Ingest (verifies inbound activity, posts placeholder, publishes job to Pub/Sub)"
  labels      = var.labels

  build_config {
    runtime     = var.ingest_runtime
    entry_point = "gti_bot_ingest_http"
    source {
      storage_source {
        bucket = google_storage_bucket.source_bucket.name
        object = google_storage_bucket_object.ingest_source.name
      }
    }
  }

  service_config {
    available_cpu                    = var.ingest_cpu
    available_memory                 = var.ingest_memory
    max_instance_count               = var.ingest_max_instances
    min_instance_count               = var.ingest_min_instances
    max_instance_request_concurrency = var.ingest_concurrency
    timeout_seconds                  = var.ingest_timeout_seconds
    service_account_email            = google_service_account.ingest_sa.email
    ingress_settings                 = "ALLOW_ALL"
    all_traffic_on_latest_revision   = true

    environment_variables = {
      THREADS        = tostring(var.ingest_concurrency)
      GCP_PROJECT_ID = var.project_id
      CLIENT_ID      = azuread_application.bot_app.client_id
      TENANT_ID      = data.azuread_client_config.current.tenant_id
      PUBSUB_TOPIC   = google_pubsub_topic.job_topic.name
      # Changes whenever client_secret is rotated → forces a new Cloud Run
      # revision so already-running instances pick up the new value
      # (secret_environment_variables with version = "latest" only resolves
      # at deploy time, not live).
      SECRET_VERSION_TRIGGER = google_secret_manager_secret_version.client_secret_version.version
    }

    secret_environment_variables {
      key        = "CLIENT_SECRET"
      project_id = var.project_id
      secret     = google_secret_manager_secret.client_secret.secret_id
      version    = "latest"
    }
  }

  lifecycle {
    precondition {
      condition     = var.ingest_min_instances <= var.ingest_max_instances
      error_message = "ingest_min_instances must be less than or equal to ingest_max_instances."
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.client_secret_version,
  ]
}

# Publicly reachable — the bot's own JWT check in app/teams/auth.py gates
# /api/messages, not IAM (contrast worker_invoker below, which restricts the
# worker to the Pub/Sub push identity only).
resource "google_cloud_run_v2_service_iam_member" "ingest_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.ingest.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# bot-worker-function — Cloud Run function (2nd Gen), built from source
# -----------------------------------------------------------------------------
resource "google_cloudfunctions2_function" "worker" {
  name        = "${var.bot_name}-worker"
  location    = var.region
  description = "GTI Teams Bot — Worker (Pub/Sub push target: thread context, GTI Agentic query, delivery)"
  labels      = var.labels

  build_config {
    runtime     = var.worker_runtime
    entry_point = "gti_bot_worker_http"
    source {
      storage_source {
        bucket = google_storage_bucket.source_bucket.name
        object = google_storage_bucket_object.worker_source.name
      }
    }
  }

  service_config {
    available_cpu                    = var.worker_cpu
    available_memory                 = var.worker_memory
    max_instance_count               = var.worker_max_instances
    min_instance_count               = var.worker_min_instances
    max_instance_request_concurrency = var.worker_concurrency
    # Matches job_subscription's ack_deadline_seconds below exactly — see
    # var.worker_timeout_seconds' own description for why 600s is a hard
    # ceiling here, not just this module's default.
    timeout_seconds                = var.worker_timeout_seconds
    service_account_email          = google_service_account.worker_sa.email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    environment_variables = {
      THREADS                         = tostring(var.worker_concurrency)
      GCP_PROJECT_ID                  = var.project_id
      CLIENT_ID                       = azuread_application.bot_app.client_id
      TENANT_ID                       = data.azuread_client_config.current.tenant_id
      GTI_API_BASE_URL                = var.gti_api_base_url
      FIRESTORE_DATABASE              = var.firestore_database
      FIRESTORE_BOT_CONFIG_COLLECTION = var.firestore_bot_config_collection
      FIRESTORE_OUTPUT_FORMAT_DOC     = var.firestore_output_format_doc
      OUTPUT_FORMAT_INSTRUCTIONS      = var.output_format_instructions
      THREAD_CONTEXT_ENABLED          = tostring(var.thread_context_enabled)
      THREAD_CONTEXT_MESSAGE_COUNT    = tostring(var.thread_context_message_count)
      # Changes whenever either secret is rotated → forces a new Cloud Run
      # revision so already-running instances pick up the new secret value.
      SECRET_VERSION_TRIGGER = "${google_secret_manager_secret_version.gti_api_key_version.version}-${google_secret_manager_secret_version.client_secret_version.version}"
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

  lifecycle {
    precondition {
      condition     = var.worker_min_instances <= var.worker_max_instances
      error_message = "worker_min_instances must be less than or equal to worker_max_instances."
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.gti_api_key_version,
    google_secret_manager_secret_version.client_secret_version,
  ]
}

# NOT public — restricted to the Pub/Sub push identity only. This is the
# worker's entire authentication mechanism: there is no per-request bearer
# token check in app code the way the ingest function has (contrast
# app/teams/auth.py, which the worker doesn't even import) — Cloud Run IAM
# is what gates this function.
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_push_identity.email}"
}

# -----------------------------------------------------------------------------
# Pub/Sub: job hand-off from ingest to worker
# -----------------------------------------------------------------------------
# Deliberately NOT using google_cloudfunctions2_function's native
# event_trigger (Eventarc) binding for this: Eventarc auto-manages its own
# Pub/Sub subscription and doesn't cleanly expose a Terraform-attachable
# dead_letter_policy on it. A manually-declared topic + push subscription
# (below) gives full control over retry/dead-letter behavior — the closest
# analog to Azure Storage Queue's maxDequeueCount + auto-created poison
# queue — at the cost of the worker being a plain authenticated HTTP
# function instead of a native trigger. See README.md for the full rationale.

resource "google_pubsub_topic" "job_topic" {
  name = "${var.bot_name}-jobs"

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "dead_letter_topic" {
  name = "${var.bot_name}-jobs-dlq"

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "job_subscription" {
  name  = "${var.bot_name}-jobs-sub"
  topic = google_pubsub_topic.job_topic.id

  # MUST be explicit — if omitted this defaults to 10 seconds (verified
  # against the Terraform provider's own docs), which would redeliver every
  # real job to a second instance 10 seconds after the first started
  # working on it. 600 is Pub/Sub's own hard ceiling for a push
  # subscription — there is no lease-renewal mechanism available to a push
  # endpoint the way Azure Storage Queue's peek-lock has, so this really is
  # the maximum runway this design can ever have per job (see
  # var.worker_timeout_seconds' description and the README's timeout-budget
  # section).
  ack_deadline_seconds = 600

  push_config {
    push_endpoint = "${google_cloudfunctions2_function.worker.service_config[0].uri}/tasks/process"
    oidc_token {
      service_account_email = google_service_account.pubsub_push_identity.email
      # The function's base URL, NOT the full /tasks/process path — Cloud
      # Run/Functions gen2's own invoker-auth check expects the service's
      # base URL as the audience.
      audience = google_cloudfunctions2_function.worker.service_config[0].uri
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter_topic.id
    max_delivery_attempts = var.max_delivery_attempts
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  depends_on = [google_project_service.apis]
}

# Mirrors Azure's "<queue>-poison": nothing consumes dead_letter_topic
# automatically the way Azure's runtime auto-creates and auto-routes to the
# poison queue — this second push subscription is what makes
# app/poison_handler.py actually run. No dead_letter_policy of its own
# (poison-of-poison is out of scope, matching Azure — a poison delivery
# failure is just logged, see main.py's _handle_poison for why it always
# acks with 200 regardless of outcome).
resource "google_pubsub_subscription" "dead_letter_subscription" {
  name  = "${var.bot_name}-jobs-dlq-sub"
  topic = google_pubsub_topic.dead_letter_topic.id

  # Shorter than job_subscription's — poison_handler.py does no GTI call,
  # just a best-effort Teams notification.
  ack_deadline_seconds = 60

  push_config {
    push_endpoint = "${google_cloudfunctions2_function.worker.service_config[0].uri}/tasks/poison"
    oidc_token {
      service_account_email = google_service_account.pubsub_push_identity.email
      audience              = google_cloudfunctions2_function.worker.service_config[0].uri
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  depends_on = [google_project_service.apis]
}

# ── IAM: three distinct grants, easy to conflate — kept separate ────────────

# 1. Dead-letter mechanics: the Pub/Sub SERVICE AGENT (not the push
#    identity) needs to publish into the DLQ topic and consume off the
#    source subscription to forward messages there.
resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  topic  = google_pubsub_topic.dead_letter_topic.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${local.pubsub_service_agent_email}"

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription_iam_member" "source_subscriber" {
  subscription = google_pubsub_subscription.job_subscription.id
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${local.pubsub_service_agent_email}"

  depends_on = [google_project_service.apis]
}

# 2. OIDC push-auth signing: the service agent needs to mint tokens AS the
#    push identity for push authentication to work at all. Usually already
#    covered by the service agent's default roles/pubsub.serviceAgent role
#    on projects created after April 8, 2021 (verified against GCP's push
#    auth docs) — included explicitly anyway since it's harmless, idempotent,
#    and removes a footgun for deployment into any older project.
resource "google_service_account_iam_member" "pubsub_push_identity_token_creator" {
  service_account_id = google_service_account.pubsub_push_identity.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${local.pubsub_service_agent_email}"

  depends_on = [google_project_service.apis]
}

# 3. Ingest needs to publish onto the job topic at all — distinct from the
#    push identity, which is Pub/Sub's OUTBOUND identity toward the worker,
#    not ingest's own runtime identity.
resource "google_pubsub_topic_iam_member" "ingest_can_publish" {
  topic  = google_pubsub_topic.job_topic.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.ingest_sa.email}"
}

# (Invocation IAM — pubsub_push_identity's roles/run.invoker on the worker —
# is declared above, right next to the worker function it applies to.)

# -----------------------------------------------------------------------------
# Azure Bot Service & Teams Channel
# -----------------------------------------------------------------------------
data "azurerm_resource_group" "existing_bot_rg" {
  count = var.azure_resource_group_name != "" ? 1 : 0
  name  = var.azure_resource_group_name
}

resource "azurerm_resource_group" "bot_rg" {
  count    = var.azure_resource_group_name == "" ? 1 : 0
  name     = "${var.bot_name}-rg"
  location = var.azure_region
  tags     = var.labels
}

locals {
  bot_resource_group_name = var.azure_resource_group_name != "" ? data.azurerm_resource_group.existing_bot_rg[0].name : azurerm_resource_group.bot_rg[0].name
  azure_bot_name          = var.azure_bot_name != "" ? var.azure_bot_name : "${var.bot_name}-${random_id.suffix.hex}"
}

resource "azurerm_bot_service_azure_bot" "bot" {
  name                    = local.azure_bot_name
  resource_group_name     = local.bot_resource_group_name
  location                = "global"
  sku                     = "S1"
  microsoft_app_id        = azuread_application.bot_app.client_id
  microsoft_app_type      = "SingleTenant"
  microsoft_app_tenant_id = data.azurerm_client_config.current.tenant_id
  # Points at the INGEST function, never the worker — the worker has no
  # public HTTP surface at all (see worker_invoker above).
  endpoint = "${google_cloudfunctions2_function.ingest.service_config[0].uri}/api/messages"
  tags     = var.labels
}

resource "azurerm_bot_channel_ms_teams" "teams" {
  bot_name            = azurerm_bot_service_azure_bot.bot.name
  location            = azurerm_bot_service_azure_bot.bot.location
  resource_group_name = local.bot_resource_group_name
}
