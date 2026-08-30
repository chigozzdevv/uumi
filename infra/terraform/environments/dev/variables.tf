variable "project_id" {
  description = "Google Cloud project that owns Uumi."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project ID."
  }
}

variable "agent_project_id" {
  description = "Separate project for Agent Runtime, Gateway, Registry, Model Armor, staging, and CMEK."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.agent_project_id == null ||
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.agent_project_id))
    )
    error_message = "agent_project_id must be null or a valid Google Cloud project ID."
  }
}

variable "agent_access_token" {
  description = "Optional ephemeral OAuth token for the isolated agent-project provider."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "deployer_access_token" {
  description = "Optional ephemeral OAuth token for Terraform provider operations."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "agent_broker_uri" {
  description = "Private Uumi MCP broker URI registered as governed agent egress."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.agent_broker_uri == null ||
      can(regex("^https://[A-Za-z0-9.-]+$", var.agent_broker_uri))
    )
    error_message = "agent_broker_uri must be null or an HTTPS origin without a path."
  }
}

variable "enable_legacy_gateway" {
  description = "Temporarily retain the unsupported in-perimeter gateway while the split project is verified."
  type        = bool
  default     = false
}

variable "region" {
  description = "Shared region for Uumi control-plane and agent resources."
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

variable "agent_model_location" {
  description = "Gemini model endpoint location for the managed agent fleet."
  type        = string
  default     = "us"

  validation {
    condition     = contains(["global", "us", "eu"], var.agent_model_location)
    error_message = "agent_model_location must be global, us, or eu."
  }
}

variable "enable_gateway" {
  description = "Enable Agent Gateway and Agent Registry APIs."
  type        = bool
  default     = true
}

variable "access_policy_id" {
  description = "Organisation Access Context Manager policy ID; required for runtime deployment."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.access_policy_id == null || can(regex("^[0-9]+$", var.access_policy_id))
    error_message = "access_policy_id must be null or numeric."
  }
}

variable "operator_access_level" {
  description = "Access level admitting authorised deployment operators to perimeter resources."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.operator_access_level == null ||
      can(regex("^accessPolicies/[0-9]+/accessLevels/[A-Za-z][A-Za-z0-9_]+$", var.operator_access_level))
    )
    error_message = "operator_access_level must be null or a full Access Context Manager access level name."
  }
}

variable "browser_allowed_domains" {
  description = "Exact provider domains allowed through the browser Secure Web Proxy."
  type        = set(string)
  default     = []
}

variable "runtime_connector_domains" {
  description = "Provider API domains admitted through the runtime Secure Web Proxy."
  type        = set(string)
  default     = []
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
    error_message = "Workflow organisation IDs must satisfy the Uumi identifier contract."
  }
}

variable "api_image" {
  description = "Immutable Uumi API image reference; null bootstraps the registry only."
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

variable "web_image" {
  description = "Immutable authenticated web gateway image reference; null leaves the web boundary disabled."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.web_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.web_image))
    )
    error_message = "web_image must be null or an immutable sha256 image reference."
  }
}

variable "publisher_image" {
  description = "Immutable Uumi publisher image reference; null leaves delivery disabled."
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
  description = "Immutable Uumi incident ingestion image reference."
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
  description = "Immutable Uumi notification worker image reference."
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
  description = "Immutable Uumi audit log publisher image reference."
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

variable "demo_image" {
  description = "Immutable Uumi Resend demo image reference."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.demo_image == null ||
      can(regex("^[^[:space:]]+@sha256:[a-f0-9]{64}$", var.demo_image))
    )
    error_message = "demo_image must be null or an immutable sha256 image reference."
  }
}

variable "notification_app_url" {
  description = "Authenticated Uumi application origin used for safe notification links."
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

variable "notification_email_secret_version" {
  description = "Immutable Secret Manager version holding Uumi's email delivery credential."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.notification_email_secret_version == null ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.notification_email_secret_version))
    )
    error_message = "notification_email_secret_version must be null or an immutable Secret Manager version."
  }
}

variable "notification_email_sender" {
  description = "Verified sender address used by Uumi email notifications."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.notification_email_sender == null ||
      can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.notification_email_sender))
    )
    error_message = "notification_email_sender must be null or a valid email address."
  }
}

variable "github_app_slug" {
  description = "Public slug of the customer-facing GitHub App."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.github_app_slug == null || can(regex("^[A-Za-z0-9-]+$", var.github_app_slug))
    error_message = "github_app_slug must be null or a GitHub App slug."
  }
}

variable "github_client_id" {
  description = "Public OAuth client ID of the GitHub App."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.github_client_id == null || can(regex("^[A-Za-z0-9._-]+$", var.github_client_id))
    error_message = "github_client_id must be null or a GitHub OAuth client ID."
  }
}

variable "github_client_secret_version" {
  description = "Secret Manager version holding the GitHub App OAuth client secret."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.github_client_secret_version == null ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.github_client_secret_version))
    )
    error_message = "github_client_secret_version must be null or an immutable Secret Manager version."
  }
}

variable "github_callback_url" {
  description = "HTTPS callback URL registered on the GitHub App."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.github_callback_url == null ||
      can(regex("^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[^?#]*)?(?:\\?[^#]*)?$", var.github_callback_url))
    )
    error_message = "github_callback_url must be null or an HTTPS URL without credentials or a fragment."
  }
}

variable "google_cloud_client_id" {
  description = "Public OAuth client ID used for Google Cloud onboarding."
  type        = string
  default     = null
  nullable    = true
}

variable "google_cloud_client_secret_version" {
  description = "Secret Manager version holding the Google OAuth client secret."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.google_cloud_client_secret_version == null ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.google_cloud_client_secret_version))
    )
    error_message = "google_cloud_client_secret_version must be null or an immutable Secret Manager version."
  }
}

variable "google_cloud_callback_url" {
  description = "HTTPS callback URL registered on the Google OAuth client."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.google_cloud_callback_url == null ||
      can(regex("^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[^?#]*)?(?:\\?[^#]*)?$", var.google_cloud_callback_url))
    )
    error_message = "google_cloud_callback_url must be null or an HTTPS URL without credentials or a fragment."
  }
}

variable "github_webhook_secret_version" {
  description = "Secret Manager version holding the global GitHub App webhook secret."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.github_webhook_secret_version == null ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.github_webhook_secret_version))
    )
    error_message = "github_webhook_secret_version must be null or an immutable Secret Manager version."
  }
}

variable "notification_secrets" {
  description = "Existing Secret Manager credentials the notification worker may access."
  type = map(object({
    project_id = string
    secret_id  = string
  }))
  default = {}

  validation {
    condition = alltrue([
      for secret in values(var.notification_secrets) :
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", secret.project_id)) &&
      can(regex("^[A-Za-z0-9_-]{1,255}$", secret.secret_id))
    ])
    error_message = "Notification secrets require valid project and secret identifiers."
  }
}

variable "scc_sources" {
  description = "SCC organisation sources keyed by Uumi organisation ID."
  type = map(object({
    cloud_organisation_id = string
    filter                = string
    location              = optional(string, "global")
  }))
  default = {}
}

variable "secret_sources" {
  description = "Uumi organisations receiving Secret Manager event notifications."
  type        = set(string)
  default     = []
}

variable "provider_sources" {
  description = "Signed provider webhook sources keyed by an operator-owned label."
  type = map(object({
    organisation_id = string
    provider        = string
  }))
  default = {}
}

variable "rotation_schedules" {
  description = "Recurring credential rotations keyed by stable schedule ID."
  type = map(object({
    organisation_id = string
    credential_id   = string
    schedule        = string
    time_zone       = optional(string, "Etc/UTC")
  }))
  default = {}
}

variable "broker_image" {
  description = "Immutable Uumi MCP broker image reference."
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
  description = "Immutable Uumi stage coordinator image reference."
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
  description = "Immutable Uumi browser worker image reference."
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
  description = "Immutable Uumi browser gateway image reference."
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

  validation {
    condition = (
      var.capability_secret_version == null ||
      can(regex("^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*$", var.capability_secret_version))
    )
    error_message = "capability_secret_version must be null or an immutable Secret Manager version."
  }
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

variable "identity_platform_domains" {
  description = "Domains allowed to complete Identity Platform sign-in redirects, including the Uumi client origin."
  type        = list(string)
  default     = []
}

variable "browser_setup_url" {
  description = "Uumi frontend route that completes a short-lived browser setup session."
  type        = string
  default     = "https://uumi.web.app/browser/setup"
}

variable "oidc_audience" {
  description = "Stable audience used by Cloud Run and Uumi token verification."
  type        = string
  default     = "https://api.uumi.internal"
}
