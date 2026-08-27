variable "project_id" {
  description = "Google Cloud project hosting managed Agent Runtime artifacts."
  type        = string
}

variable "project_number" {
  description = "Numeric project identifier used to grant Google-managed service identities."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.project_number))
    error_message = "project_number must be numeric."
  }
}

variable "location" {
  description = "Region shared by Agent Runtime, staging storage, and CMEK."
  type        = string
}

variable "deployment_member" {
  description = "Service-account member allowed to stage and deploy managed agents."
  type        = string

  validation {
    condition     = startswith(var.deployment_member, "serviceAccount:")
    error_message = "deployment_member must be an explicit service-account IAM member."
  }
}
