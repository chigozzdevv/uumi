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

variable "broker_service_account" {
  description = "Service account email assigned to the MCP broker."
  type        = string
}

variable "coordinator_service_account" {
  description = "Service account email assigned to the stage coordinator."
  type        = string
}

variable "ingestion_service_account" {
  description = "Service account email assigned to the incident ingestion revision."
  type        = string
}

variable "notification_service_account" {
  description = "Service account email assigned to the notification worker."
  type        = string
}

variable "auditlog_service_account" {
  description = "Service account email assigned to the audit log publisher."
  type        = string
}

variable "scc_push_service_account" {
  description = "Service account email asserted on SCC Pub/Sub push requests."
  type        = string
}


variable "coordinator_member" {
  description = "Coordinator IAM member allowed to invoke the MCP broker."
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

variable "github_app_slug" {
  description = "Public slug of the customer-facing GitHub App."
  type        = string
  default     = ""

  validation {
    condition     = var.github_app_slug == "" || can(regex("^[A-Za-z0-9-]+$", var.github_app_slug))
    error_message = "github_app_slug must be empty or a GitHub App slug."
  }
}

variable "github_client_id" {
  description = "Public GitHub App OAuth client ID."
  type        = string
  default     = ""

  validation {
    condition     = var.github_client_id == "" || can(regex("^[A-Za-z0-9._-]+$", var.github_client_id))
    error_message = "github_client_id must be empty or a GitHub OAuth client ID."
  }
}

variable "github_client_secret_version" {
  description = "Secret Manager version holding the GitHub App OAuth client secret."
  type        = string
  default     = ""

  validation {
    condition = (
      var.github_client_secret_version == "" ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.github_client_secret_version))
    )
    error_message = "github_client_secret_version must be empty or an immutable Secret Manager version."
  }
}

variable "github_callback_url" {
  description = "HTTPS callback URL registered on the GitHub App."
  type        = string
  default     = ""

  validation {
    condition = (
      var.github_callback_url == "" ||
      can(regex("^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[^?#]*)?(?:\\?[^#]*)?$", var.github_callback_url))
    )
    error_message = "github_callback_url must be empty or an HTTPS URL without credentials or a fragment."
  }
}

variable "github_webhook_secret_version" {
  description = "Secret Manager version holding the global GitHub App webhook secret."
  type        = string
  default     = ""

  validation {
    condition = (
      var.github_webhook_secret_version == "" ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.github_webhook_secret_version))
    )
    error_message = "github_webhook_secret_version must be empty or an immutable Secret Manager version."
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

variable "ingestion_image" {
  description = "Immutable incident ingestion image reference."
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

variable "notification_image" {
  description = "Immutable notification worker image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.notification_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.notification_image))
    )
    error_message = "notification_image must be null or an immutable sha256 image reference."
  }
}

variable "auditlog_image" {
  description = "Immutable audit log publisher image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.auditlog_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.auditlog_image))
    )
    error_message = "auditlog_image must be null or an immutable sha256 image reference."
  }
}

variable "notification_app_url" {
  description = "Authenticated application origin used in notification links."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.notification_app_url == null ||
      can(regex("^https://[a-zA-Z0-9.-]+$", var.notification_app_url))
    )
    error_message = "notification_app_url must be null or an HTTPS origin without a path."
  }
}

variable "broker_image" {
  description = "Immutable MCP broker image reference."
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
  description = "Immutable stage coordinator image reference."
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
  description = "Immutable browser worker image reference."
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

variable "browser_gateway_url" {
  description = "IAP protected browser gateway URL exposed in short-lived access grants."
  type        = string
  default     = "https://browser-gateway.disabled.invalid"
}

variable "evidence_bucket" {
  description = "Locked evidence bucket name."
  type        = string
}

variable "walkthrough_bucket" {
  description = "Short-retention bucket receiving teaching walkthrough uploads."
  type        = string
}

variable "capability_secret_version" {
  description = "Full Secret Manager version holding the capability signing key."
  type        = string

  validation {
    condition     = can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.capability_secret_version))
    error_message = "capability_secret_version must be an immutable Secret Manager version."
  }
}

variable "capability_public_key" {
  description = "Base64url Ed25519 public key used by capability verifiers."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{43}$", var.capability_public_key))
    error_message = "capability_public_key must be a raw 32-byte Ed25519 key encoded as base64url."
  }
}

variable "browser_template" {
  description = "Ephemeral browser VM instance template."
  type        = string
}

variable "browser_zone" {
  description = "Zone used for ephemeral browser VMs."
  type        = string
}

variable "network" {
  description = "VPC network used by coordinator and browser workers."
  type        = string
}

variable "subnetwork" {
  description = "Private VPC subnetwork used by coordinator and browser workers."
  type        = string
}
