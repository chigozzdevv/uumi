variable "project_id" {
  description = "Google Cloud project that owns the Firestore database."
  type        = string
}

variable "location" {
  description = "Firestore location colocated with the FireKey control plane."
  type        = string
}

variable "users" {
  description = "IAM members allowed to transact with the primary database."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for member in values(var.users) : startswith(member, "serviceAccount:")
    ])
    error_message = "Firestore users must be service account IAM members."
  }
}
