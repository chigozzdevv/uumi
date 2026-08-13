variable "project_id" {
  description = "Google Cloud project hosting FireKey agent governance."
  type        = string
}

variable "region" {
  description = "Region shared by Agent Runtime, Agent Gateway, Registry, and Model Armor."
  type        = string
}

variable "agent_principal_set" {
  description = "Project-scoped Agent Identity principal set."
  type        = string

  validation {
    condition     = startswith(var.agent_principal_set, "principalSet://agents.global.")
    error_message = "agent_principal_set must be a Google Agent Identity principal set."
  }
}

variable "broker_uri" {
  description = "Private FireKey MCP broker URI, or null before runtime deployment."
  type        = string
  default     = null
  nullable    = true
}
