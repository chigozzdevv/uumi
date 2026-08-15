output "issuer" {
  description = "Issuer of Identity Platform identity tokens for this project."
  value       = "https://securetoken.google.com/${var.project_id}"
}
