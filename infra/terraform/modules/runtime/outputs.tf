output "api_uri" {
  description = "Private Cloud Run API URI, or null until an image is deployed."
  value       = try(google_cloud_run_v2_service.api["api"].uri, null)
}

output "repository" {
  description = "Docker repository prefix for FireKey images."
  value       = "${google_artifact_registry_repository.runtime.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.runtime.repository_id}"
}
