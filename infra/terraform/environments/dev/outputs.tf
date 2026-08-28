output "region" {
  description = "Region selected for Uumi."
  value       = var.region
}

output "services" {
  description = "APIs managed for the Uumi project."
  value       = module.project.services
}

output "database_id" {
  description = "Primary Firestore database resource name."
  value       = module.storage.database_id
}

output "service_accounts" {
  description = "Uumi service account emails keyed by account ID."
  value       = module.identity.emails
}

output "api_uri" {
  description = "Private Uumi API URI, or null before the first image deployment."
  value       = module.runtime.api_uri
}

output "web_uri" {
  description = "Authenticated Uumi web gateway URI, or null before deployment."
  value       = module.runtime.web_uri
}

output "identity_platform_issuer" {
  description = "Issuer of Identity Platform identity tokens for this project."
  value       = module.identityplatform.issuer
}

output "publisher_uri" {
  description = "Private Uumi publisher URI, or null before the first image deployment."
  value       = module.runtime.publisher_uri
}

output "ingestion_uri" {
  description = "GitHub and SCC incident ingestion endpoint."
  value       = module.runtime.ingestion_uri
}

output "notification_uri" {
  description = "Private durable notification worker URI."
  value       = module.runtime.notification_uri
}

output "auditlog_uri" {
  description = "Private canonical audit publisher URI."
  value       = module.runtime.auditlog_uri
}

output "event_topic" {
  description = "Ordered Uumi run event topic."
  value       = module.events.topic
}

output "scc_topics" {
  description = "SCC finding topics keyed by Uumi organisation."
  value       = module.events.scc_topics
}

output "scc_deadletter_subscription" {
  description = "Retained SCC messages that exhausted delivery retries."
  value       = module.events.scc_deadletter_subscription
}

output "notification_deadletter_subscription" {
  description = "Retained notification events that exhausted delivery retries."
  value       = module.events.notification_deadletter_subscription
}

output "audit_deadletter_subscription" {
  description = "Retained canonical audit events that exhausted delivery retries."
  value       = module.events.audit_deadletter_subscription
}

output "operational_alerts" {
  description = "Monitoring policies for delivery paths requiring operator intervention."
  value       = module.events.alert_policies
}

output "audit_log_bucket" {
  description = "Locked regional Cloud Logging bucket for canonical audit events."
  value       = module.storage.audit_log_bucket
}

output "github_webhook_secret" {
  description = "Global GitHub App HMAC secret resource requiring an external version."
  value       = module.storage.github_webhook_secret
}

output "github_oauth_secret" {
  description = "GitHub App OAuth client secret resource requiring an external version."
  value       = module.storage.github_oauth_secret
}

output "image_repository" {
  description = "Artifact Registry repository prefix for Uumi images."
  value       = module.runtime.repository
}

output "agent_staging_bucket" {
  description = "Bucket used by the managed Agent Runtime deployment script."
  value = local.split_agent_project ? (
    module.agentstorage[0].bucket
  ) : module.storage.agent_bucket
}

output "agent_kms_key" {
  description = "CMEK used by managed agents."
  value = local.split_agent_project ? (
    module.agentstorage[0].kms_key
  ) : module.storage.kms_key
}

output "agent_ingress_gateway" {
  description = "Model Armor governed gateway for client-to-agent calls."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].ingress_gateway, null)
  ) : try(module.governance[0].ingress_gateway, null)
}

output "agent_egress_gateway" {
  description = "Identity and Model Armor governed gateway for agent egress."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].egress_gateway, null)
  ) : try(module.governance[0].egress_gateway, null)
}

output "agent_model_armor" {
  description = "Model Armor prompt template applied to the managed agent fleet."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].model_armor_template, null)
  ) : try(module.governance[0].model_armor_template, null)
}

output "agent_model_armor_response" {
  description = "Model Armor response template applied to the managed agent fleet."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].model_armor_response_template, null)
  ) : try(module.governance[0].model_armor_response_template, null)
}

output "agent_endpoints" {
  description = "Agent Registry resources allowed through the egress gateway."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].registered_endpoints, {})
  ) : try(module.governance[0].registered_endpoints, {})
}

output "agent_broker" {
  description = "MCP broker registration governed by the agent egress gateway."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].registered_broker, null)
  ) : try(module.governance[0].registered_broker, null)
}

output "agent_caller_role" {
  description = "Custom role applied to approved callers on each managed agent deployment."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].caller_role, null)
  ) : try(module.governance[0].caller_role, null)
}

output "agent_deployer_role" {
  description = "Least-privilege role used by the managed agent deployment identity."
  value = local.split_agent_project ? (
    try(module.agent_governance[0].deployer_role, null)
  ) : try(module.governance[0].deployer_role, null)
}

output "agent_project" {
  description = "Project hosting the governed managed agent fleet."
  value       = local.agent_project_id
}

output "browser_template" {
  description = "One-run Computer Use VM template."
  value       = module.browser.template
}

output "browser_egress_gateway" {
  description = "No persistent browser egress gateway is deployed."
  value       = module.browser.egress_gateway
}

output "browser_egress_domains" {
  description = "No browser egress domains are active while Computer Use is disabled."
  value       = module.browser.egress_domains
}

output "service_perimeter" {
  description = "Enforced VPC Service Controls perimeter, or null before configuration."
  value       = try(module.perimeter[0].name, null)
}

output "location_policy" {
  description = "Project resource-location policy, or null before perimeter configuration."
  value       = try(module.perimeter[0].location_policy, null)
}

output "workflow" {
  description = "Authoritative rotation workflow resource."
  value       = module.workflow.name
}

output "browser_gateway" {
  description = "IAP protected browser view and takeover URL."
  value       = module.gateway.url
}
