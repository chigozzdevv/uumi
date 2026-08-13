variable "project_id" {
  description = "Google Cloud project that owns FireKey event delivery."
  type        = string
}

variable "region" {
  description = "Region used for ordered publishing and event delivery."
  type        = string
}

variable "publisher_member" {
  description = "Publisher workload IAM member."
  type        = string
}

variable "event_member" {
  description = "Event delivery workload IAM member."
  type        = string
}

variable "event_service_account" {
  description = "Event delivery service account email."
  type        = string
}

variable "publisher_name" {
  description = "Cloud Run publisher service name."
  type        = string
  default     = null
  nullable    = true
}

variable "publisher_uri" {
  description = "Cloud Run publisher service URI."
  type        = string
  default     = null
  nullable    = true
}
