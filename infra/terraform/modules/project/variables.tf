variable "project_id" {
  description = "Google Cloud project that owns FireKey."
  type        = string
}

variable "enable_gateway" {
  description = "Enable the APIs required by Agent Gateway and Agent Registry."
  type        = bool
  default     = true
}

