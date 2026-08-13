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
    "firekey-ingestion" = {
      display_name = "FireKey Incident Ingestion"
      description  = "Authenticates and correlates GitHub and SCC exposure events."
    }
    "firekey-publisher" = {
      display_name = "FireKey Publisher"
      description  = "Claims and publishes durable run events."
    }
    "firekey-workflow" = {
      display_name = "FireKey Workflow"
      description  = "Invokes authorised FireKey workflow transitions."
    }
    "firekey-broker" = {
      display_name = "FireKey MCP Broker"
      description  = "Executes capability-scoped provider and runtime tools."
    }
    "firekey-coordinator" = {
      display_name = "FireKey Stage Coordinator"
      description  = "Executes and proves each deterministic rotation stage."
    }
    "firekey-browser" = {
      display_name = "FireKey Browser Worker"
      description  = "Runs isolated one-run Computer Use browser sessions."
    }
    "firekey-gateway" = {
      display_name = "FireKey Browser Gateway"
      description  = "Provides authorised live view and human takeover."
    }
    "firekey-agents" = {
      display_name = "FireKey Agent Runtime"
      description  = "Runs the registered ADK reasoning fleet."
    }
  }

  depends_on = [module.project]
}

resource "google_service_account_iam_member" "event_workflow" {
  service_account_id = module.identity.emails["firekey-workflow"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = module.identity.members["firekey-events"]
}

resource "google_service_account_iam_member" "workflow_token" {
  service_account_id = module.identity.emails["firekey-workflow"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = module.identity.members["firekey-workflow"]
}

resource "google_service_account_iam_member" "coordinator_token" {
  service_account_id = module.identity.emails["firekey-coordinator"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = module.identity.members["firekey-coordinator"]
}

module "storage" {
  source = "../../modules/storage"

  project_id = var.project_id
  location   = var.region
  users = {
    api         = module.identity.members["firekey-api"]
    ingestion   = module.identity.members["firekey-ingestion"]
    publisher   = module.identity.members["firekey-publisher"]
    broker      = module.identity.members["firekey-broker"]
    coordinator = module.identity.members["firekey-coordinator"]
    browser     = module.identity.members["firekey-browser"]
    gateway     = module.identity.members["firekey-gateway"]
    agents      = module.identity.members["firekey-agents"]
  }
  evidence_users = {
    broker      = module.identity.members["firekey-broker"]
    coordinator = module.identity.members["firekey-coordinator"]
    browser     = module.identity.members["firekey-browser"]
  }
  walkthrough_user   = module.identity.members["firekey-api"]
  agent_staging_user = module.identity.members["firekey-agents"]
  secret_accessors = {
    api         = module.identity.members["firekey-api"]
    coordinator = module.identity.members["firekey-coordinator"]
  }
  github_organisations     = var.workflow_organisations
  github_secret_accessor   = module.identity.members["firekey-ingestion"]
  provider_sources         = var.provider_sources
  provider_secret_accessor = module.identity.members["firekey-ingestion"]
  principals = {
    for organisation_id in var.workflow_organisations :
    "workflow-${organisation_id}" => {
      organisation_id = organisation_id
      subject         = module.identity.subjects["firekey-workflow"]
      roles           = ["automation"]
    }
  }

  depends_on = [module.project]
}

module "browser" {
  source = "../../modules/browser"

  project_id             = var.project_id
  region                 = var.region
  zone                   = var.zone
  worker_service_account = module.identity.emails["firekey-browser"]
  coordinator_member     = module.identity.members["firekey-coordinator"]

  depends_on = [module.project, module.identity, module.storage]
}

locals {
  capability_secret_version = coalesce(
    var.capability_secret_version,
    "${module.storage.capability_secret}/versions/1"
  )
  runtime_images = compact([
    var.api_image,
    var.publisher_image,
    var.ingestion_image,
    var.broker_image,
    var.coordinator_image,
    var.browser_image,
    var.gateway_image,
  ])
}

check "complete_runtime" {
  assert {
    condition = length(local.runtime_images) == 0 || (
      length(local.runtime_images) == 7 &&
      var.capability_secret_version != null &&
      length(var.workflow_organisations) > 0 &&
      length(var.gateway_users) > 0
    )
    error_message = "Deploy all seven runtime images together with an explicit capability secret, organisation grant, and IAP gateway user."
  }
}

check "scc_tenants" {
  assert {
    condition     = setsubtract(toset(keys(var.scc_sources)), var.workflow_organisations) == toset([])
    error_message = "Every SCC source must map to an authorised FireKey organisation."
  }
}

check "ingestion_tenants" {
  assert {
    condition = (
      setsubtract(var.secret_sources, var.workflow_organisations) == toset([]) &&
      setsubtract(
        toset([for source in values(var.provider_sources) : source.organisation_id]),
        var.workflow_organisations,
      ) == toset([]) &&
      setsubtract(
        toset([for schedule in values(var.rotation_schedules) : schedule.organisation_id]),
        var.workflow_organisations,
      ) == toset([])
    )
    error_message = "Every ingestion source must map to an authorised FireKey organisation."
  }
}

module "runtime" {
  source = "../../modules/runtime"

  project_id                  = var.project_id
  region                      = var.region
  api_service_account         = module.identity.emails["firekey-api"]
  ingestion_service_account   = module.identity.emails["firekey-ingestion"]
  publisher_service_account   = module.identity.emails["firekey-publisher"]
  broker_service_account      = module.identity.emails["firekey-broker"]
  coordinator_service_account = module.identity.emails["firekey-coordinator"]
  coordinator_member          = module.identity.members["firekey-coordinator"]
  workflow_member             = module.identity.members["firekey-workflow"]
  event_member                = module.identity.members["firekey-events"]
  scc_push_service_account    = module.identity.emails["firekey-events"]
  oidc_audience               = var.oidc_audience
  api_image                   = var.api_image
  ingestion_image             = var.ingestion_image
  publisher_image             = var.publisher_image
  broker_image                = var.broker_image
  coordinator_image           = var.coordinator_image
  browser_image               = var.browser_image
  browser_gateway_url         = coalesce(module.gateway.url, "https://browser-gateway.disabled.invalid")
  evidence_bucket             = module.storage.evidence_bucket
  walkthrough_bucket          = module.storage.walkthrough_bucket
  capability_secret_version   = local.capability_secret_version
  capability_public_key       = var.capability_public_key
  browser_template            = module.browser.template
  browser_zone                = var.zone
  network                     = module.browser.network
  subnetwork                  = module.browser.subnetwork

  depends_on = [module.project, module.storage, module.browser, module.gateway]
}

module "gateway" {
  source = "../../modules/gateway"

  project_id            = var.project_id
  region                = var.region
  image                 = var.gateway_image
  service_account       = module.identity.emails["firekey-gateway"]
  capability_public_key = var.capability_public_key
  network               = module.browser.network
  subnetwork            = module.browser.subnetwork
  users                 = var.gateway_users

  depends_on = [module.project, module.browser, module.storage]
}

module "events" {
  source = "../../modules/events"

  project_id            = var.project_id
  region                = var.region
  publisher_member      = module.identity.members["firekey-publisher"]
  event_member          = module.identity.members["firekey-events"]
  event_service_account = module.identity.emails["firekey-events"]
  secretmanager_member  = module.storage.secretmanager_member
  publisher_name        = module.runtime.publisher_name
  publisher_uri         = module.runtime.publisher_uri
  ingestion_uri         = module.runtime.ingestion_uri
  oidc_audience         = var.oidc_audience
  scc_sources           = var.scc_sources
  secret_sources        = var.secret_sources
  rotation_schedules    = var.rotation_schedules

  depends_on = [module.project, module.runtime]
}

module "workflow" {
  source = "../../modules/workflow"

  project_id            = var.project_id
  region                = var.region
  service_account       = module.identity.emails["firekey-workflow"]
  event_service_account = module.identity.emails["firekey-events"]
  event_topic           = module.events.topic
  api_url               = module.runtime.api_uri
  coordinator_url       = module.runtime.coordinator_uri
  oidc_audience         = var.oidc_audience

  depends_on = [module.project, module.events, module.runtime]
}

resource "google_project_iam_member" "agent_runtime" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["firekey-agents"]
}

resource "google_project_iam_member" "browser_runtime" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["firekey-browser"]
}

resource "google_project_iam_member" "api_runtime" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/logging.logWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["firekey-api"]
}

resource "google_project_iam_member" "coordinator_runtime" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/logging.viewer",
    "roles/monitoring.viewer",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["firekey-coordinator"]
}

resource "google_project_iam_member" "ingestion_runtime" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["firekey-ingestion"]
}
