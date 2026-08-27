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

output "modelarmor_endpoint" {
  description = "Private regional endpoint for Model Armor screening."
  value       = google_network_connectivity_regional_endpoint.modelarmor.id
}

output "template" {
  description = "One-run browser instance template resource name."
  value       = "projects/${var.project_id}/global/instanceTemplates/${google_compute_instance_template.browser.name}"
}

output "egress_gateway" {
  description = "No persistent browser egress gateway is deployed."
  value       = null
}

output "egress_domains" {
  description = "No browser egress domains are active while Computer Use is disabled."
  value       = []
}
