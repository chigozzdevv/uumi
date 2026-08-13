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

output "agent_bucket" {
  description = "Agent Runtime source staging bucket URI."
  value       = "gs://${google_storage_bucket.agents.name}"
}

output "capability_secret" {
  description = "Capability signing secret resource; add a version outside Terraform."
  value       = google_secret_manager_secret.capability.id
}

output "github_secrets" {
  description = "GitHub webhook secret resources keyed by FireKey organisation."
  value       = { for organisation, secret in google_secret_manager_secret.github : organisation => secret.id }
}

output "kms_key" {
  description = "CMEK resource used by evidence and Agent Runtime."
  value       = google_kms_crypto_key.evidence.id
}
