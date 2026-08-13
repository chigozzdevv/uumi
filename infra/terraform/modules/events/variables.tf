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

variable "ingestion_uri" {
  description = "Cloud Run incident ingestion URI."
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
