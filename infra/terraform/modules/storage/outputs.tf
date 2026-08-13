output "database_id" {
  description = "Fully qualified Firestore database resource name."
  value       = google_firestore_database.primary.id
}

output "database_name" {
  description = "Firestore database ID used by server clients."
  value       = google_firestore_database.primary.name
}
