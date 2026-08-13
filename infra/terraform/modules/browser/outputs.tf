output "network" {
  description = "FireKey browser VPC resource name."
  value       = google_compute_network.firekey.id
}

output "subnetwork" {
  description = "Private browser subnetwork resource name."
  value       = google_compute_subnetwork.browser.id
}

output "template" {
  description = "One-run browser instance template resource name."
  value       = google_compute_instance_template.browser.self_link
}
