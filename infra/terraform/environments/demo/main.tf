module "project" {
  source = "../../modules/project"

  project_id     = var.project_id
  enable_gateway = var.enable_gateway
}

