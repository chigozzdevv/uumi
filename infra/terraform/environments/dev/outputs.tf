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

output "ingestion_uri" {
  description = "GitHub and SCC incident ingestion endpoint."
  value       = module.runtime.ingestion_uri
}

output "event_topic" {
  description = "Ordered FireKey run event topic."
  value       = module.events.topic
}

output "scc_topics" {
  description = "SCC finding topics keyed by FireKey organisation."
  value       = module.events.scc_topics
}

output "scc_deadletter_subscription" {
  description = "Retained SCC messages that exhausted delivery retries."
  value       = module.events.scc_deadletter_subscription
}

output "github_webhook_secrets" {
  description = "GitHub HMAC secret resources requiring externally supplied versions."
  value       = module.storage.github_secrets
}

output "image_repository" {
  description = "Artifact Registry repository prefix for FireKey images."
  value       = module.runtime.repository
}

output "agent_staging_bucket" {
  description = "Bucket used by the managed Agent Runtime deployment script."
  value       = module.storage.agent_bucket
}

output "agent_kms_key" {
  description = "CMEK used by managed agents."
  value       = module.storage.kms_key
}

output "browser_template" {
  description = "One-run Computer Use VM template."
  value       = module.browser.template
}

output "workflow" {
  description = "Authoritative rotation workflow resource."
  value       = module.workflow.name
}

output "browser_gateway" {
  description = "IAP protected browser view and takeover URL."
  value       = module.gateway.url
}
