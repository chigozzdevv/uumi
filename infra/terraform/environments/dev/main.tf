module "project" {
  source = "../../modules/project"

  project_id     = var.project_id
  enable_gateway = var.enable_gateway
}

module "identity" {
  source = "../../modules/identity"

  project_id = var.project_id
  accounts = {
    "firekey-api" = {
      display_name = "FireKey API"
      description  = "Runs the private FireKey control-plane API."
    }
    "firekey-events" = {
      display_name = "FireKey Event Delivery"
      description  = "Invokes the outbox publisher from Eventarc and Cloud Scheduler."
    }
    "firekey-publisher" = {
      display_name = "FireKey Publisher"
      description  = "Claims and publishes durable run events."
    }
    "firekey-workflow" = {
      display_name = "FireKey Workflow"
      description  = "Invokes authorised FireKey workflow transitions."
    }
  }

  depends_on = [module.project]
}

module "storage" {
  source = "../../modules/storage"

  project_id = var.project_id
  location   = var.region
  users = {
    api       = module.identity.members["firekey-api"]
    publisher = module.identity.members["firekey-publisher"]
  }
  principals = {
    for organisation_id in var.workflow_organisations :
    "workflow-${organisation_id}" => {
      organisation_id = organisation_id
      subject         = module.identity.subjects["firekey-workflow"]
      roles           = ["operator"]
    }
  }

  depends_on = [module.project]
}

module "runtime" {
  source = "../../modules/runtime"

  project_id                = var.project_id
  region                    = var.region
  api_service_account       = module.identity.emails["firekey-api"]
  publisher_service_account = module.identity.emails["firekey-publisher"]
  workflow_member           = module.identity.members["firekey-workflow"]
  event_member              = module.identity.members["firekey-events"]
  oidc_audience             = var.oidc_audience
  api_image                 = var.api_image
  publisher_image           = var.publisher_image

  depends_on = [module.project, module.storage]
}

module "events" {
  source = "../../modules/events"

  project_id            = var.project_id
  region                = var.region
  publisher_member      = module.identity.members["firekey-publisher"]
  event_member          = module.identity.members["firekey-events"]
  event_service_account = module.identity.emails["firekey-events"]
  publisher_name        = module.runtime.publisher_name
  publisher_uri         = module.runtime.publisher_uri

  depends_on = [module.project, module.runtime]
}
