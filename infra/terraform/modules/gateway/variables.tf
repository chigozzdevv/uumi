variable "project_id" {
  description = "Google Cloud project hosting the browser gateway."
  type        = string
}

variable "region" {
  description = "Cloud Run region."
  type        = string
}

variable "image" {
  description = "Immutable browser gateway image."
  type        = string
  nullable    = true
}

variable "service_account" {
  description = "Browser gateway service account email."
  type        = string
}

variable "capability_secret_version" {
  description = "Capability signing Secret Manager version."
  type        = string
}

variable "network" {
  description = "Browser worker VPC."
  type        = string
}

variable "subnetwork" {
  description = "Browser worker subnetwork."
  type        = string
}

variable "users" {
  description = "IAM members allowed to view and take over browser sessions."
  type        = set(string)
  default     = []
}
