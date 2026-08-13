variable "project_id" {
  description = "Google Cloud project that owns the Firestore database."
  type        = string
}

variable "location" {
  description = "Firestore location colocated with the FireKey control plane."
  type        = string
}

variable "users" {
  description = "IAM members allowed to transact with the primary database."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for member in values(var.users) : startswith(member, "serviceAccount:")
    ])
    error_message = "Firestore users must be service account IAM members."
  }
}

variable "principals" {
  description = "Organisation-scoped workload grants managed with the database."
  type = map(object({
    organisation_id = string
    subject         = string
    roles           = set(string)
  }))
  default = {}

  validation {
    condition = alltrue([
      for grant in values(var.principals) :
      can(regex("^[a-z][a-z0-9_-]{2,127}$", grant.organisation_id)) &&
      length(grant.subject) > 0 &&
      length(grant.roles) > 0 &&
      alltrue([
        for role in grant.roles :
        contains(["viewer", "operator", "administrator", "automation"], role)
      ])
    ])
    error_message = "Principal grants require a valid organisation, subject, and supported role."
  }
}

variable "evidence_users" {
  description = "IAM members allowed to append and read immutable evidence objects."
  type        = map(string)
  default     = {}
}

variable "agent_staging_user" {
  description = "Managed Agent Runtime IAM member allowed to use its staging bucket and CMEK."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.agent_staging_user == null ||
      startswith(var.agent_staging_user, "serviceAccount:")
    )
    error_message = "Agent staging user must be a service account IAM member."
  }
}

variable "secret_accessors" {
  description = "IAM members allowed to read the capability signing key."
  type        = map(string)
  default     = {}
}

variable "github_organisations" {
  description = "FireKey organisations receiving signed GitHub webhooks."
  type        = set(string)
  default     = []
}

variable "github_secret_accessor" {
  description = "IAM member allowed to verify organisation GitHub webhook signatures."
  type        = string
  default     = null
  nullable    = true
}
