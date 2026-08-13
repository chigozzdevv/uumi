output "region" {
  description = "Region selected for FireKey."
  value       = var.region
}

output "services" {
  description = "APIs managed for the FireKey project."
  value       = module.project.services
}

output "database_id" {
  description = "Primary Firestore database resource name."
  value       = module.storage.database_id
}

output "service_accounts" {
  description = "FireKey service account emails keyed by account ID."
  value       = module.identity.emails
}

output "api_uri" {
  description = "Private FireKey API URI, or null before the first image deployment."
  value       = module.runtime.api_uri
}

output "publisher_uri" {
  description = "Private FireKey publisher URI, or null before the first image deployment."
  value       = module.runtime.publisher_uri
}

output "event_topic" {
  description = "Ordered FireKey run event topic."
  value       = module.events.topic
}

output "image_repository" {
  description = "Artifact Registry repository prefix for FireKey images."
  value       = module.runtime.repository
}
