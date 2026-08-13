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
