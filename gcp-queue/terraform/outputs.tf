# =============================================================================
# GTI Teams Bot (Agentic) — GCP Queue-Decoupled Terraform Outputs
# =============================================================================

output "ingest_function_name" {
  description = "Name of the deployed bot-ingest-function Cloud Run function."
  value       = google_cloudfunctions2_function.ingest.name
}

output "ingest_function_url" {
  description = "Base URL of the ingest Cloud Run function."
  value       = google_cloudfunctions2_function.ingest.service_config[0].uri
}

output "gti_bot_messaging_endpoint" {
  description = "Microsoft Teams Bot messaging webhook endpoint to configure in the Azure Bot / Bot Framework registration (already wired automatically into azurerm_bot_service_azure_bot.bot below — this output is diagnostic)."
  value       = "${google_cloudfunctions2_function.ingest.service_config[0].uri}/api/messages"
}

output "worker_function_name" {
  description = "Name of the deployed bot-worker-function Cloud Run function."
  value       = google_cloudfunctions2_function.worker.name
}

output "worker_function_url" {
  description = "Base URL of the worker Cloud Run function. Diagnostic only — never public, and never given to anything external; Pub/Sub is the only caller, authenticated via the push identity's OIDC token."
  value       = google_cloudfunctions2_function.worker.service_config[0].uri
}

output "job_topic_name" {
  description = "Pub/Sub topic bot-ingest-function publishes jobs to."
  value       = google_pubsub_topic.job_topic.name
}

output "dead_letter_topic_name" {
  description = "Pub/Sub topic jobs land on after exhausting max_delivery_attempts."
  value       = google_pubsub_topic.dead_letter_topic.name
}

output "gcs_source_bucket" {
  description = "Name of the Google Cloud Storage bucket storing function source code and Teams manifest zip."
  value       = google_storage_bucket.source_bucket.name
}

output "teams_manifest_zip_gcs_url" {
  description = "GCS URI for the ready-to-sideload Microsoft Teams app manifest ZIP."
  value       = "gs://${google_storage_bucket.source_bucket.name}/${google_storage_bucket_object.teams_manifest_zip.name}"
}

output "firestore_database_id" {
  description = "ID of the Google Cloud Firestore database used for sessions, config, and redelivery dedup claims."
  value       = var.create_firestore_database ? google_firestore_database.database[0].name : var.firestore_database
}

output "secret_gti_api_key_name" {
  description = "Secret Manager secret name for the Google Threat Intelligence API key."
  value       = google_secret_manager_secret.gti_api_key.secret_id
}

output "secret_client_secret_name" {
  description = "Secret Manager secret name for the Microsoft App Client Secret."
  value       = google_secret_manager_secret.client_secret.secret_id
}

output "post_deployment_instructions" {
  description = "Manual steps required after 'terraform apply' to finish setting up the bot."
  value       = <<-EOT
  =============================================================================
  GTI TEAMS BOT (QUEUE-DECOUPLED) — POST-DEPLOYMENT MANUAL STEPS
  =============================================================================

  1. SIDELOAD THE TEAMS APP MANIFEST:
     gsutil cp gs://${google_storage_bucket.source_bucket.name}/${google_storage_bucket_object.teams_manifest_zip.name} ./gti-teams-bot-queue-manifest.zip
     Then in Microsoft Teams: Apps -> Upload a custom app
     (or Teams Admin Center -> Manage apps -> Upload new app, for org-wide rollout).

  %{if var.thread_context_enabled~}
  2. GRANT MICROSOFT GRAPH PERMISSION FOR CHANNEL THREAD CONTEXT:
     (thread_context_enabled = true, so this is required)
     Azure Portal -> Microsoft Entra ID -> App registrations -> your bot app
       -> API permissions -> Add a permission -> Microsoft Graph -> Application permissions
       -> ChannelMessage.Read.All -> Add permissions
       -> "Grant admin consent for <tenant>" (requires a tenant admin).
  %{else~}
  2. (skipped: thread_context_enabled = false — no Graph permission needed)
  %{endif~}

  3. VERIFY:
     curl "${google_cloudfunctions2_function.ingest.service_config[0].uri}/health"
     Expect a 200 response. The worker's own /health is not publicly
     reachable (roles/run.invoker is restricted to the Pub/Sub push
     identity) — check it instead via Cloud Logging, or temporarily grant
     yourself roles/run.invoker on ${google_cloudfunctions2_function.worker.name} to curl it directly.

  4. SEND A TEST MESSAGE in Teams and confirm: a placeholder appears
     immediately, then is replaced by the real GTI response a few seconds to
     a few minutes later. If the placeholder never updates, check
     bot-worker-function's logs first — a job that failed all
     max_delivery_attempts (${var.max_delivery_attempts}) shows up as a
     dead-letter push handled by app/poison_handler.py.
  =============================================================================
  EOT
}
