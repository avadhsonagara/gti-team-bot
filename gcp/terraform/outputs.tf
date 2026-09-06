# =============================================================================
# GTI Teams Bot (Agentic) — GCP Terraform Outputs
# =============================================================================

output "gti_bot_name" {
  description = "Name of the deployed gti-bot Cloud Run v2 service."
  value       = google_cloud_run_v2_service.gti_bot.name
}

output "gti_bot_url" {
  description = "Base URL of the gti-bot Cloud Run v2 service."
  value       = google_cloud_run_v2_service.gti_bot.uri
}

output "gti_bot_messaging_endpoint" {
  description = "Microsoft Teams Bot messaging webhook endpoint to configure in the Microsoft Azure Bot / Bot Framework registration."
  value       = "${google_cloud_run_v2_service.gti_bot.uri}/api/messages"
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
  value       = google_firestore_database.database.name
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
