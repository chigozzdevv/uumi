output "emails" {
  description = "Service account email addresses keyed by account ID."
  value       = { for key, account in google_service_account.account : key => account.email }
}

output "members" {
  description = "IAM member names keyed by account ID."
  value       = { for key, account in google_service_account.account : key => account.member }
}
