variable "project_id" {
  description = "Google Cloud project that owns Uumi."
  type        = string
}

variable "enable_gateway" {
  description = "Enable the APIs required by Agent Gateway and Agent Registry."
  type        = bool
  default     = true
}

variable "service_overrides" {
  description = "Exact API set to enable instead of the default Uumi control-plane services."
  type        = set(string)
  default     = null
  nullable    = true

  validation {
    condition = (
      var.service_overrides == null ||
      alltrue([for service in var.service_overrides : endswith(service, ".googleapis.com")])
    )
    error_message = "service_overrides must contain Google API service names."
  }
}
