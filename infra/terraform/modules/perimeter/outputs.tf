output "name" {
  description = "Enforced Uumi VPC Service Controls perimeter."
  value       = google_access_context_manager_service_perimeter.uumi.name
}

output "restricted_services" {
  description = "Google APIs protected by the Uumi service perimeter."
  value       = local.restricted_services
}

output "location_policy" {
  description = "Project policy restricting supported resources to the selected region."
  value       = google_org_policy_policy.locations.name
}
