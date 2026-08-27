output "bucket" {
  description = "Agent Runtime source staging bucket URI."
  value       = "gs://${google_storage_bucket.agents.name}"
}

output "kms_key" {
  description = "CMEK protecting managed Agent Runtime and its staging artifacts."
  value       = google_kms_crypto_key.agents.id
}
