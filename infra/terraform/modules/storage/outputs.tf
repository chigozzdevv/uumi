output "database_id" {
  description = "Fully qualified Firestore database resource name."
  value       = google_firestore_database.primary.id
}

output "database_name" {
  description = "Firestore database ID used by server clients."
  value       = google_firestore_database.primary.name
}

output "evidence_bucket" {
  description = "Locked evidence bucket name."
  value       = google_storage_bucket.evidence.name
}

output "audit_log_bucket" {
  description = "Locked regional Cloud Logging bucket for canonical audit events."
  value       = google_logging_project_bucket_config.audit.id
}

output "walkthrough_bucket" {
  description = "Short-retention bucket for non-production teaching walkthroughs."
  value       = google_storage_bucket.walkthroughs.name
}

output "agent_bucket" {
  description = "Agent Runtime source staging bucket URI."
  value       = "gs://${google_storage_bucket.agents.name}"
}

output "capability_secret" {
  description = "Capability signing secret resource; add a version outside Terraform."
  value       = google_secret_manager_secret.capability.id
}

output "github_webhook_secret" {
  description = "GitHub App webhook secret resource; add a version outside Terraform."
  value       = google_secret_manager_secret.github_webhook.id
}

output "github_oauth_secret" {
  description = "GitHub App OAuth client secret resource; add a version outside Terraform."
  value       = google_secret_manager_secret.github_oauth.id
}

output "google_cloud_oauth_secret" {
  description = "Google Cloud OAuth client secret resource; add a version outside Terraform."
  value       = google_secret_manager_secret.google_cloud_oauth.id
}

output "provider_secrets" {
  description = "Provider webhook secret resources keyed by configured source."
  value       = { for source, secret in google_secret_manager_secret.provider : source => secret.id }
}

output "kms_key" {
  description = "CMEK resource used by evidence and Agent Runtime."
  value       = google_kms_crypto_key.evidence.id
}

output "secretmanager_member" {
  description = "Secret Manager service agent IAM member."
  value       = google_project_service_identity.secretmanager.member
}
