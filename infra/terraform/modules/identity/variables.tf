variable "project_id" {
  description = "Google Cloud project that owns the service identities."
  type        = string
}

variable "accounts" {
  description = "Uumi service accounts keyed by stable account ID."
  type = map(object({
    display_name = string
    description  = string
  }))

  validation {
    condition = alltrue([
      for account_id in keys(var.accounts) :
      can(regex("^[a-z]([a-z0-9-]{4,28}[a-z0-9])$", account_id))
    ])
    error_message = "Every account ID must be a valid 6 to 30 character service account ID."
  }
}
