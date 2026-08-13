output "topic" {
  description = "Ordered FireKey run event topic ID."
  value       = google_pubsub_topic.events.id
}

output "topic_id" {
  description = "Fully qualified Pub/Sub event topic resource."
  value       = google_pubsub_topic.events.id
}

output "scc_topics" {
  description = "SCC finding topics keyed by FireKey organisation."
  value       = { for organisation, topic in google_pubsub_topic.scc : organisation => topic.id }
}

output "scc_deadletter_subscription" {
  description = "Retained SCC messages that exhausted delivery retries."
  value       = try(google_pubsub_subscription.deadletter[0].id, null)
}
