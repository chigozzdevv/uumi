output "bucket_name" {
  description = "Bucket used by environment backends."
  value       = google_storage_bucket.state.name
}

