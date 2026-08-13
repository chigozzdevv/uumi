output "services" {
  description = "APIs managed for the FireKey project."
  value       = sort([for service in google_project_service.service : service.service])
}

