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
  value       = google_model_armor_template.firekey.name
}

output "registered_endpoints" {
  description = "Approved Agent Registry endpoints keyed by destination."
  value       = { for name, endpoint in google_agent_registry_service.endpoint : name => endpoint.registry_resource }
}

output "registered_broker" {
  description = "FireKey MCP server registered for governed agent egress."
  value       = try(google_agent_registry_service.broker[0].registry_resource, null)
}
