variable "project_id" {
  description = "Google Cloud project hosting Uumi agent governance."
  type        = string
}

variable "region" {
  description = "Region shared by Agent Runtime, Agent Gateway, Registry, and Model Armor."
  type        = string
}

variable "model_location" {
  description = "Gemini model endpoint location governed by Agent Gateway."
  type        = string

  validation {
    condition     = contains(["global", "us", "eu"], var.model_location)
    error_message = "model_location must be global, us, or eu."
  }
}

variable "agent_principal_set" {
  description = "Project-scoped Agent Identity principal set."
  type        = string

  validation {
    condition     = startswith(var.agent_principal_set, "principalSet://agents.global.")
    error_message = "agent_principal_set must be a Google Agent Identity principal set."
  }
}

variable "deployment_member" {
  description = "Service-account member that deploys agents and applies per-agent caller IAM."
  type        = string

  validation {
    condition     = startswith(var.deployment_member, "serviceAccount:")
    error_message = "deployment_member must be an explicit service-account IAM member."
  }
}

variable "model_armor_callers" {
  description = "Service-account members allowed to sanitize Uumi agent prompts and responses directly."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for member in var.model_armor_callers : startswith(member, "serviceAccount:")
    ])
    error_message = "model_armor_callers must contain only service-account IAM members."
  }
}

variable "broker_uri" {
  description = "Private Uumi MCP broker URI, or null before runtime deployment."
  type        = string
  default     = null
  nullable    = true
}
