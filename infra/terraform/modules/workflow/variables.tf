variable "project_id" {
  description = "Google Cloud project that owns the rotation workflow."
  type        = string
}

variable "region" {
  description = "Region for Workflows and Eventarc."
  type        = string
}

variable "service_account" {
  description = "Service account email used by workflow executions."
  type        = string
}

variable "event_service_account" {
  description = "Service account email used by Eventarc delivery."
  type        = string
}

variable "event_topic" {
  description = "Full Pub/Sub topic resource carrying run events."
  type        = string
}

variable "enabled" {
  description = "Whether the rotation workflow is part of this deployment."
  type        = bool
}

variable "api_url" {
  description = "Private Uumi control API URL."
  type        = string
  nullable    = true
}

variable "coordinator_url" {
  description = "Private Uumi stage coordinator URL."
  type        = string
  nullable    = true
}

variable "oidc_audience" {
  description = "Stable control API OIDC audience."
  type        = string
}
