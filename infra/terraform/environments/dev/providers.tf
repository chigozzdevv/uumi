provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

provider "google" {
  alias                 = "agent"
  access_token          = var.agent_access_token
  project               = coalesce(var.agent_project_id, var.project_id)
  region                = var.region
  billing_project       = coalesce(var.agent_project_id, var.project_id)
  user_project_override = true
}

provider "google-beta" {
  alias                 = "agent"
  access_token          = var.agent_access_token
  project               = coalesce(var.agent_project_id, var.project_id)
  region                = var.region
  billing_project       = coalesce(var.agent_project_id, var.project_id)
  user_project_override = true
}
