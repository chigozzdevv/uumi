output "topic" {
  description = "Ordered FireKey run event topic ID."
  value       = google_pubsub_topic.events.id
}
