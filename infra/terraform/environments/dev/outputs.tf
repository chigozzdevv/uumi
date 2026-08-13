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

output "agent_ingress_gateway" {
  description = "Model Armor governed gateway for client-to-agent calls."
  value       = try(module.governance[0].ingress_gateway, null)
}

output "agent_egress_gateway" {
  description = "Identity and Model Armor governed gateway for agent egress."
  value       = try(module.governance[0].egress_gateway, null)
}

output "agent_model_armor" {
  description = "Model Armor template applied to the managed agent fleet."
  value       = try(module.governance[0].model_armor_template, null)
}

output "agent_endpoints" {
  description = "Agent Registry resources allowed through the egress gateway."
  value       = try(module.governance[0].registered_endpoints, {})
}

output "agent_broker" {
  description = "MCP broker registration governed by the agent egress gateway."
  value       = try(module.governance[0].registered_broker, null)
}

output "agent_caller_role" {
  description = "Custom role applied to approved callers on each managed agent deployment."
  value       = try(module.governance[0].caller_role, null)
}

output "agent_deployer_role" {
  description = "Least-privilege role used by the managed agent deployment identity."
  value       = try(module.governance[0].deployer_role, null)
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
