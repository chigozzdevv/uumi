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
    api = module.identity.members["firekey-api"]
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
