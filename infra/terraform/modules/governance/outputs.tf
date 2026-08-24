output "ingress_gateway" {
  description = "Agent Gateway resource governing client-to-agent traffic."
  value       = google_network_services_agent_gateway.ingress.id
}

output "egress_gateway" {
  description = "Agent Gateway resource governing agent-to-anywhere traffic."
  value       = google_network_services_agent_gateway.egress.id
}

output "model_armor_template" {
  description = "Fail-closed Model Armor template bound to both gateways."
  value       = google_model_armor_template.uumi.name
}

output "registered_endpoints" {
  description = "Approved Agent Registry endpoints keyed by destination."
  value       = { for name, endpoint in google_agent_registry_service.endpoint : name => endpoint.registry_resource }
}

output "registered_broker" {
  description = "Uumi MCP server registered for governed agent egress."
  value       = try(google_agent_registry_service.broker[0].registry_resource, null)
}

output "caller_role" {
  description = "Least-privilege role bound to approved callers on each managed agent."
  value       = google_project_iam_custom_role.caller.name
}

output "deployer_role" {
  description = "Least-privilege role used to apply per-agent caller IAM."
  value       = google_project_iam_custom_role.deployer.name
}
