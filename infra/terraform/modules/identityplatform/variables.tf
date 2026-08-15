variable "project_id" {
  description = "Google Cloud project hosting Identity Platform sign-in."
  type        = string
}

variable "authorized_domains" {
  description = "Domains allowed to complete Identity Platform sign-in redirects. localhost is always permitted."
  type        = list(string)
  default     = []
}
