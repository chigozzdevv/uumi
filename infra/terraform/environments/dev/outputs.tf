output "region" {
  description = "Region selected for FireKey."
  value       = var.region
}

output "services" {
  description = "APIs managed for the FireKey project."
  value       = module.project.services
}
