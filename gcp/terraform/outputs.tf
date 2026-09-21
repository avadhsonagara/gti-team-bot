# =============================================================================
# GTI Teams Bot (Agentic) — GCP Terraform Outputs
# =============================================================================

output "gti_bot_name" {
  description = "Name of the deployed gti-bot Cloud Run function."
  value       = google_cloudfunctions2_function.gti_bot.name
}

output "gti_bot_url" {
  description = "Base URL of the gti-bot Cloud Run function."
  value       = google_cloudfunctions2_function.gti_bot.service_config[0].uri
}

output "gti_bot_messaging_endpoint" {
  description = "Microsoft Teams Bot messaging webhook endpoint to configure in the Microsoft Azure Bot / Bot Framework registration."
  value       = "${google_cloudfunctions2_function.gti_bot.service_config[0].uri}/api/messages"
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
  description = "ID of the Google Cloud Firestore database used for config and state storage."
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

output "rs_alerts_enabled" {
  description = "Whether RS Alerts background worker is provisioned."
  value       = var.enable_rs_alerts
}

output "rs_alerts_function_url" {
  description = "Base URL of the RS Alerts Cloud Run function (if enabled)."
  value       = var.enable_rs_alerts ? google_cloudfunctions2_function.rs_alerts[0].service_config[0].uri : null
}

output "rs_alerts_scheduler_job_name" {
  description = "Name of the Cloud Scheduler job triggering RS Alerts (if enabled)."
  value       = var.enable_rs_alerts ? google_cloud_scheduler_job.rs_alerts_schedule[0].name : null
}

output "post_deployment_instructions" {
  description = "Manual steps required after 'terraform apply' to finish setting up the bot."
  value       = <<-EOT
  =============================================================================
  GTI TEAMS BOT — POST-DEPLOYMENT MANUAL STEPS
  =============================================================================

  1. SIDELOAD THE TEAMS APP MANIFEST:
     gsutil cp gs://${google_storage_bucket.source_bucket.name}/${google_storage_bucket_object.teams_manifest_zip.name} ./teams-app-manifest.zip
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

  %{if var.enable_rs_alerts~}
  3. ADD THE BOT TO THE RS ALERTS TARGET TEAM:
     (enable_rs_alerts = true, so this is required)
     RS Alerts has no auto-install step — sideload the manifest (step 1) into
     the team/channel that rs_alerts_teams_channel_id_or_link points to.
     Message delivery will fail with a "bot not in conversation" error until
     the bot is a member of that team.
  %{else~}
  3. (skipped: enable_rs_alerts = false)
  %{endif~}

  4. VERIFY:
     curl "${google_cloudfunctions2_function.gti_bot.service_config[0].uri}/health"
     Expect "gti_api_configured": true in the response.
  =============================================================================
  EOT
}
