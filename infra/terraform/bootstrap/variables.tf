variable "project_id" {
  description = "Google Cloud project that owns the Terraform state bucket."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project ID."
  }
}

variable "bucket_name" {
  description = "Globally unique bucket name for FireKey Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must satisfy Cloud Storage bucket naming rules."
  }
}

variable "region" {
  description = "Region used for the state bucket."
  type        = string
  default     = "us-east1"
}

