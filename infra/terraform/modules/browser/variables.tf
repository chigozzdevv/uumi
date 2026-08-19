variable "project_id" {
  description = "Google Cloud project hosting isolated browser workers."
  type        = string
}

variable "region" {
  description = "Region for the browser VPC."
  type        = string
}

variable "zone" {
  description = "Zone for ephemeral browser VMs."
  type        = string
}

variable "worker_service_account" {
  description = "Service account email assigned to browser VMs."
  type        = string
}

variable "coordinator_member" {
  description = "Coordinator IAM member allowed to create and delete browser VMs."
  type        = string
}

variable "allowed_domains" {
  description = "Exact external provider domains allowed through browser Secure Web Proxy."
  type        = set(string)

  validation {
    condition = length(var.allowed_domains) > 0 && alltrue([
      for domain in var.allowed_domains :
      can(regex("^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", domain))
    ])
    error_message = "allowed_domains must contain at least one lowercase DNS hostname without wildcards or paths."
  }
}

variable "connector_domains" {
  description = "External provider API domains allowed from FireKey runtime connectors."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for domain in var.connector_domains :
      can(regex("^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", domain))
    ])
    error_message = "connector_domains must contain lowercase DNS hostnames without wildcards or paths."
  }
}
