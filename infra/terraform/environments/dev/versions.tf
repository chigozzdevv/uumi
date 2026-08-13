terraform {
  required_version = ">= 1.15.8, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.41"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.41"
    }
  }
}
