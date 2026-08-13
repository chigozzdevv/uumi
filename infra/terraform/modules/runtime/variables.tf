variable "project_id" {
  description = "Google Cloud project that owns FireKey runtime resources."
  type        = string
}

variable "region" {
  description = "Region for the image repository and API service."
  type        = string
}

variable "api_service_account" {
  description = "Service account email assigned to the API revision."
  type        = string
}

variable "publisher_service_account" {
  description = "Service account email assigned to the publisher revision."
  type        = string
}

variable "event_member" {
  description = "Event delivery IAM member allowed to invoke the publisher."
  type        = string

  validation {
    condition     = startswith(var.event_member, "serviceAccount:")
    error_message = "event_member must be a service account IAM member."
  }
}

variable "event_topic" {
  description = "Pub/Sub topic receiving ordered run events."
  type        = string
  default     = "firekey-events"
}

variable "workflow_member" {
  description = "Workflow IAM member allowed to invoke the private API."
  type        = string

  validation {
    condition     = startswith(var.workflow_member, "serviceAccount:")
    error_message = "workflow_member must be a service account IAM member."
  }
}

variable "oidc_audience" {
  description = "Stable custom audience required on API identity tokens."
  type        = string

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+$", var.oidc_audience))
    error_message = "oidc_audience must be an HTTPS origin without a path."
  }
}

variable "api_image" {
  description = "Immutable API image reference; null provisions only the image repository."
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
  description = "Immutable publisher image reference; null leaves publisher delivery disabled."
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
