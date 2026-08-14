variable "project_id" {
  description = "Google Cloud project protected by the service perimeter."
  type        = string
}

variable "project_number" {
  description = "Numeric Google Cloud project identifier protected by the perimeter."
  type        = string
}

variable "access_policy_id" {
  description = "Organisation Access Context Manager policy containing the FireKey perimeter."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.access_policy_id))
    error_message = "access_policy_id must be numeric."
  }
}

variable "operator_access_level" {
  description = "Full Access Context Manager access level for authorised deployment operators."
  type        = string

  validation {
    condition     = can(regex("^accessPolicies/[0-9]+/accessLevels/[A-Za-z][A-Za-z0-9_]+$", var.operator_access_level))
    error_message = "operator_access_level must be a full Access Context Manager access level name."
  }
}

variable "region" {
  description = "Only Google Cloud region in which new FireKey resources may be created."
  type        = string
}
