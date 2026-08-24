output "api_uri" {
  description = "Private Cloud Run API URI, or null until an image is deployed."
  value       = try(google_cloud_run_v2_service.api["api"].uri, null)
}

output "repository" {
  description = "Docker repository prefix for Uumi images."
  value       = "${google_artifact_registry_repository.runtime.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.runtime.repository_id}"
}

output "publisher_name" {
  description = "Private Cloud Run publisher name, or null until an image is deployed."
  value       = try(google_cloud_run_v2_service.publisher["publisher"].name, null)
}

output "publisher_uri" {
  description = "Private Cloud Run publisher URI, or null until an image is deployed."
  value       = try(google_cloud_run_v2_service.publisher["publisher"].uri, null)
}

output "ingestion_uri" {
  description = "Public transport endpoint for authenticated incident ingestion."
  value       = try(google_cloud_run_v2_service.ingestion["ingestion"].uri, null)
}

output "notification_name" {
  description = "Notification worker service name when deployed."
  value       = try(google_cloud_run_v2_service.notification["notification"].name, null)
}

output "notification_uri" {
  description = "Notification worker URI when deployed."
  value       = try(google_cloud_run_v2_service.notification["notification"].uri, null)
}

output "auditlog_name" {
  description = "Audit log publisher service name when deployed."
  value       = try(google_cloud_run_v2_service.auditlog["auditlog"].name, null)
}

output "auditlog_uri" {
  description = "Audit log publisher URI when deployed."
  value       = try(google_cloud_run_v2_service.auditlog["auditlog"].uri, null)
}

output "broker_uri" {
  description = "Private MCP broker URI."
  value       = try(google_cloud_run_v2_service.broker["broker"].uri, null)
}

output "coordinator_uri" {
  description = "Private stage coordinator URI."
  value       = try(google_cloud_run_v2_service.coordinator["coordinator"].uri, null)
}
