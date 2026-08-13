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

variable "ingestion_image" {
  description = "Immutable FireKey incident ingestion image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.ingestion_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.ingestion_image))
    )
    error_message = "ingestion_image must be null or an immutable sha256 image reference."
  }
}

variable "scc_sources" {
  description = "SCC organisation sources keyed by FireKey organisation ID."
  type = map(object({
    cloud_organisation_id = string
    filter                = string
    location              = optional(string, "global")
  }))
  default = {}
}

variable "broker_image" {
  description = "Immutable FireKey MCP broker image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.broker_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.broker_image))
    )
    error_message = "broker_image must be null or an immutable sha256 image reference."
  }
}

variable "coordinator_image" {
  description = "Immutable FireKey stage coordinator image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.coordinator_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.coordinator_image))
    )
    error_message = "coordinator_image must be null or an immutable sha256 image reference."
  }
}

variable "browser_image" {
  description = "Immutable FireKey browser worker image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.browser_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.browser_image))
    )
    error_message = "browser_image must be null or an immutable sha256 image reference."
  }
}

variable "gateway_image" {
  description = "Immutable FireKey browser gateway image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.gateway_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.gateway_image))
    )
    error_message = "gateway_image must be null or an immutable sha256 image reference."
  }
}

variable "capability_secret_version" {
  description = "Capability signing Secret Manager version; defaults to version 1."
  type        = string
  default     = null
  nullable    = true
}

variable "capability_public_key" {
  description = "Base64url Ed25519 public key paired with the capability signing secret."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{43}$", var.capability_public_key))
    error_message = "capability_public_key must be a raw 32-byte Ed25519 key encoded as base64url."
  }
}

variable "zone" {
  description = "Zone for one-run Computer Use VMs."
  type        = string
  default     = "us-east1-b"
}

variable "gateway_users" {
  description = "IAM users and groups allowed to view and take over browser sessions."
  type        = set(string)
  default     = []
}

variable "oidc_audience" {
  description = "Stable audience used by Cloud Run and FireKey token verification."
  type        = string
  default     = "https://api.firekey.internal"
}
