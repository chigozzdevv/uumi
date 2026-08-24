output "network" {
  description = "Uumi browser VPC resource name."
  value       = google_compute_network.uumi.id
}

output "subnetwork" {
  description = "Private browser subnetwork resource name."
  value       = google_compute_subnetwork.browser.id
}

output "runtime_subnetwork" {
  description = "Dedicated Cloud Run Direct VPC egress subnetwork resource name."
  value       = google_compute_subnetwork.runtime.id
}

output "template" {
  description = "One-run browser instance template resource name."
  value       = "projects/${var.project_id}/global/instanceTemplates/${google_compute_instance_template.browser.name}"
}

output "egress_gateway" {
  description = "Regional default-deny Secure Web Proxy used by browser workers."
  value       = google_network_services_gateway.browser.id
}

output "egress_domains" {
  description = "Exact provider and Google domains allowed by the browser egress policy."
  value       = local.egress_domains
}
