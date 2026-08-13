variable "project_id" {
  description = "Google Cloud project that owns FireKey."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project ID."
  }
}

variable "region" {
  description = "Shared region for FireKey control-plane and agent resources."
  type        = string
  default     = "us-east1"

  validation {
    condition = contains([
      "asia-east1",
      "asia-northeast1",
      "asia-south1",
      "asia-southeast1",
      "europe-southwest1",
      "europe-west1",
      "europe-west2",
      "europe-west3",
      "europe-west4",
      "europe-west6",
      "europe-west8",
      "me-west1",
      "northamerica-northeast1",
      "southamerica-east1",
      "us-central1",
      "us-east1",
      "us-east4",
      "us-west1",
    ], var.region)
    error_message = "region must support Agent Runtime, Sessions, Memory Bank, and Agent Gateway."
  }
}

variable "enable_gateway" {
  description = "Enable Agent Gateway and Agent Registry APIs."
  type        = bool
  default     = true
}

variable "workflow_organisations" {
  description = "Organisations the workflow identity is authorised to operate."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for organisation_id in var.workflow_organisations :
      can(regex("^[a-z][a-z0-9_-]{2,127}$", organisation_id))
    ])
    error_message = "Workflow organisation IDs must satisfy the FireKey identifier contract."
  }
}

variable "api_image" {
  description = "Immutable FireKey API image reference; null bootstraps the registry only."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.api_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.api_image))
    )
    error_message = "api_image must be null or an immutable sha256 image reference."
  }
}

variable "publisher_image" {
  description = "Immutable FireKey publisher image reference; null leaves delivery disabled."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.publisher_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.publisher_image))
    )
    error_message = "publisher_image must be null or an immutable sha256 image reference."
  }
}

variable "oidc_audience" {
  description = "Stable audience used by Cloud Run and FireKey token verification."
  type        = string
  default     = "https://api.firekey.internal"
}
