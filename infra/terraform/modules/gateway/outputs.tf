output "url" {
  description = "Direct IAP-protected browser gateway URL."
  value       = try(google_cloud_run_v2_service.gateway["gateway"].uri, null)
}
