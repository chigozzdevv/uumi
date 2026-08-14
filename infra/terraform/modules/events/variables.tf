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

variable "secretmanager_member" {
  description = "Secret Manager service agent IAM member allowed to publish notifications."
  type        = string

  validation {
    condition     = startswith(var.secretmanager_member, "serviceAccount:")
    error_message = "secretmanager_member must be a service account IAM member."
  }
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

variable "ingestion_uri" {
  description = "Cloud Run incident ingestion URI."
  type        = string
  default     = null
  nullable    = true
}

variable "notification_name" {
  description = "Cloud Run notification worker service name."
  type        = string
  default     = null
  nullable    = true
}

variable "notification_uri" {
  description = "Cloud Run notification worker URI."
  type        = string
  default     = null
  nullable    = true
}

variable "auditlog_name" {
  description = "Cloud Run audit log publisher service name."
  type        = string
  default     = null
  nullable    = true
}

variable "auditlog_uri" {
  description = "Cloud Run audit log publisher URI."
  type        = string
  default     = null
  nullable    = true
}

variable "oidc_audience" {
  description = "Audience asserted by SCC Pub/Sub push identity tokens."
  type        = string
}

variable "scc_sources" {
  description = "SCC organisation sources keyed by FireKey organisation ID."
  type = map(object({
    cloud_organisation_id = string
    filter                = string
    location              = optional(string, "global")
  }))
  default = {}

  validation {
    condition = alltrue([
      for organisation, source in var.scc_sources :
      can(regex("^[a-z][a-z0-9_-]{2,127}$", organisation)) &&
      can(regex("^[0-9]+$", source.cloud_organisation_id)) &&
      can(regex("^(global|eu|[a-z]+-[a-z]+[0-9])$", source.location)) &&
      length(trimspace(source.filter)) > 0
    ])
    error_message = "SCC sources require a FireKey organisation, numeric Cloud organisation, valid location, and filter."
  }
}

variable "secret_sources" {
  description = "FireKey organisations receiving Secret Manager notifications."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for organisation in var.secret_sources :
      can(regex("^[a-z][a-z0-9_-]{2,127}$", organisation))
    ])
    error_message = "Secret sources require valid FireKey organisation identifiers."
  }
}

variable "rotation_schedules" {
  description = "Recurring FireKey credential rotations keyed by stable schedule ID."
  type = map(object({
    organisation_id = string
    credential_id   = string
    schedule        = string
    time_zone       = optional(string, "Etc/UTC")
  }))
  default = {}

  validation {
    condition = alltrue([
      for schedule_id, schedule in var.rotation_schedules :
      can(regex("^[a-z][a-z0-9_-]{2,127}$", schedule_id)) &&
      can(regex("^[a-z][a-z0-9_-]{2,127}$", schedule.organisation_id)) &&
      can(regex("^[a-z][a-z0-9_-]{2,127}$", schedule.credential_id)) &&
      length(trimspace(schedule.schedule)) > 0 &&
      length(trimspace(schedule.time_zone)) > 0
    ])
    error_message = "Rotation schedules require stable IDs, a tenant, credential, cron expression, and time zone."
  }
}
