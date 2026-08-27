locals {
  agent_project_id    = coalesce(var.agent_project_id, var.project_id)
  split_agent_project = var.enable_gateway && local.agent_project_id != var.project_id
  agent_services = toset([
    "agentregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "orgpolicy.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "telemetry.googleapis.com",
  ])
}

module "project" {
  source = "../../modules/project"

  project_id     = var.project_id
  enable_gateway = var.enable_gateway
}

module "agent_project" {
  count  = local.split_agent_project ? 1 : 0
  source = "../../modules/project"

  providers = {
    google      = google.agent
    google-beta = google-beta.agent
  }

  project_id        = local.agent_project_id
  enable_gateway    = true
  service_overrides = local.agent_services
}

resource "google_org_policy_policy" "public_iam" {
  name            = "projects/${var.project_id}/policies/iam.allowedPolicyMemberDomains"
  parent          = "projects/${var.project_id}"
  deletion_policy = "PREVENT"

  spec {
    inherit_from_parent = false

    rules {
      allow_all = "TRUE"
    }
  }

  depends_on = [module.project]
}

module "identityplatform" {
  source = "../../modules/identityplatform"

  project_id         = var.project_id
  authorized_domains = var.identity_platform_domains

  depends_on = [module.project]
}

module "identity" {
  source = "../../modules/identity"

  project_id = var.project_id
  accounts = {
    "uumi-api" = {
      display_name = "Uumi API"
      description  = "Runs the private Uumi control-plane API."
    }
    "uumi-web" = {
      display_name = "Uumi Web Gateway"
      description  = "Authenticates browser requests before invoking the private API."
    }
    "uumi-events" = {
      display_name = "Uumi Event Delivery"
      description  = "Invokes the outbox publisher from Eventarc and Cloud Scheduler."
    }
    "uumi-ingestion" = {
      display_name = "Uumi Incident Ingestion"
      description  = "Authenticates and correlates GitHub and SCC exposure events."
    }
    "uumi-publisher" = {
      display_name = "Uumi Publisher"
      description  = "Claims and publishes durable run events."
    }
    "uumi-workflow" = {
      display_name = "Uumi Workflow"
      description  = "Invokes authorised Uumi workflow transitions."
    }
    "uumi-broker" = {
      display_name = "Uumi MCP Broker"
      description  = "Executes capability-scoped provider and runtime tools."
    }
    "uumi-coordinator" = {
      display_name = "Uumi Stage Coordinator"
      description  = "Executes and proves each deterministic rotation stage."
    }
    "uumi-browser" = {
      display_name = "Uumi Browser Worker"
      description  = "Runs isolated one-run Computer Use browser sessions."
    }
    "uumi-gateway" = {
      display_name = "Uumi Browser Gateway"
      description  = "Provides authorised live view and human takeover."
    }
    "uumi-agents" = {
      display_name = "Uumi Agent Runtime"
      description  = "Runs the registered ADK reasoning fleet."
    }
    "uumi-notification" = {
      display_name = "Uumi Notification Worker"
      description  = "Delivers durable safe notifications through configured channels."
    }
    "uumi-auditlog" = {
      display_name = "Uumi Audit Log Publisher"
      description  = "Delivers canonical hash-chained audit events to locked Cloud Logging."
    }
  }

  depends_on = [module.project]
}

resource "google_service_account_iam_member" "event_workflow" {
  service_account_id = module.identity.names["uumi-workflow"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = module.identity.members["uumi-events"]
}

resource "google_service_account_iam_member" "workflow_token" {
  service_account_id = module.identity.names["uumi-workflow"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = module.identity.members["uumi-workflow"]
}

resource "google_service_account_iam_member" "coordinator_token" {
  service_account_id = module.identity.names["uumi-coordinator"]
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = module.identity.members["uumi-coordinator"]
}

module "storage" {
  source = "../../modules/storage"

  project_id = var.project_id
  location   = var.region
  users = {
    api          = module.identity.members["uumi-api"]
    ingestion    = module.identity.members["uumi-ingestion"]
    publisher    = module.identity.members["uumi-publisher"]
    broker       = module.identity.members["uumi-broker"]
    coordinator  = module.identity.members["uumi-coordinator"]
    browser      = module.identity.members["uumi-browser"]
    gateway      = module.identity.members["uumi-gateway"]
    notification = module.identity.members["uumi-notification"]
    auditlog     = module.identity.members["uumi-auditlog"]
    agents       = module.identity.members["uumi-agents"]
  }
  evidence_users = {
    api         = module.identity.members["uumi-api"]
    broker      = module.identity.members["uumi-broker"]
    coordinator = module.identity.members["uumi-coordinator"]
    browser     = module.identity.members["uumi-browser"]
  }
  walkthrough_user = module.identity.members["uumi-api"]
  walkthrough_cors_origins = toset([
    for domain in var.identity_platform_domains : "https://${domain}"
  ])
  agent_staging_user = module.identity.members["uumi-agents"]
  secret_accessors = {
    api         = module.identity.members["uumi-api"]
    coordinator = module.identity.members["uumi-coordinator"]
  }
  browser_session_organisations = var.workflow_organisations
  browser_session_user          = module.identity.members["uumi-browser"]
  browser_session_manager       = module.identity.members["uumi-api"]
  github_webhook_accessor       = module.identity.members["uumi-ingestion"]
  github_oauth_accessor         = module.identity.members["uumi-api"]
  provider_sources              = var.provider_sources
  provider_secret_accessor      = module.identity.members["uumi-ingestion"]
  principals = {
    for organisation_id in var.workflow_organisations :
    "workflow-${organisation_id}" => {
      organisation_id = organisation_id
      subject         = module.identity.subjects["uumi-workflow"]
      roles           = ["automation"]
    }
  }

  depends_on = [module.project]
}

resource "google_secret_manager_secret_iam_member" "notification" {
  for_each = var.notification_secrets

  project   = each.value.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = module.identity.members["uumi-notification"]
}

module "browser" {
  source = "../../modules/browser"

  project_id             = var.project_id
  project_number         = data.google_project.current.number
  region                 = var.region
  zone                   = var.zone
  worker_service_account = module.identity.emails["uumi-browser"]
  coordinator_member     = module.identity.members["uumi-coordinator"]
  allowed_domains        = var.browser_allowed_domains
  connector_domains      = var.runtime_connector_domains

  depends_on = [module.project]
}

locals {
  capability_secret_version = coalesce(
    var.capability_secret_version,
    "${module.storage.capability_secret}/versions/1"
  )
  control_plane_images = compact([
    var.api_image,
    var.web_image,
  ])
  automation_images = compact([
    var.publisher_image,
    var.ingestion_image,
    var.broker_image,
    var.coordinator_image,
    var.browser_image,
    var.gateway_image,
    var.notification_image,
    var.auditlog_image,
  ])
}

# These lifecycle preconditions are hard deployment gates; check blocks only emit warnings.
resource "terraform_data" "deployment" {
  input = "uumi-deployment"

  lifecycle {
    precondition {
      condition = length(local.control_plane_images) == 0 || (
        length(local.control_plane_images) == 2 &&
        var.capability_secret_version != null
      )
      error_message = "Deploy the API and authenticated web gateway together with an immutable capability secret version."
    }

    precondition {
      condition = length(local.automation_images) == 0 || (
        length(local.automation_images) == 8 &&
        length(local.control_plane_images) == 2 &&
        var.notification_app_url != null &&
        var.notification_email_secret_version != null &&
        var.notification_email_sender != null &&
        var.github_app_slug != null &&
        var.github_client_id != null &&
        var.github_client_secret_version != null &&
        var.github_callback_url != null &&
        var.github_webhook_secret_version != null &&
        var.access_policy_id != null &&
        var.operator_access_level != null &&
        length(var.browser_allowed_domains) > 0 &&
        length(var.runtime_connector_domains) > 0 &&
        length(var.workflow_organisations) > 0 &&
        length(var.gateway_users) > 0
      )
      error_message = "Deploy all eight automation images together with the control plane, GitHub App, email delivery, perimeter, browser egress, organisation grant, and IAP gateway configuration."
    }

    precondition {
      condition = alltrue([
        var.google_cloud_client_id == null,
        var.google_cloud_client_secret_version == null,
        var.google_cloud_callback_url == null,
        ]) || alltrue([
        var.google_cloud_client_id != null,
        var.google_cloud_client_secret_version != null,
        var.google_cloud_callback_url != null,
      ])
      error_message = "Google Cloud onboarding requires the client ID, client secret version, and callback URL together."
    }

    precondition {
      condition = (
        (
          var.notification_email_secret_version == null &&
          var.notification_email_sender == null
          ) || (
          var.notification_email_secret_version != null &&
          var.notification_email_sender != null &&
          anytrue([
            for secret in values(var.notification_secrets) :
            startswith(
              coalesce(var.notification_email_secret_version, ""),
              "projects/${secret.project_id}/secrets/${secret.secret_id}/versions/",
            )
          ])
        )
      )
      error_message = "Email delivery requires a sender and a secret version covered by notification_secrets IAM."
    }

    precondition {
      condition = (
        var.access_policy_id == null ||
        var.operator_access_level == null ||
        startswith(var.operator_access_level, "accessPolicies/${var.access_policy_id}/")
      )
      error_message = "operator_access_level must belong to access_policy_id."
    }

    precondition {
      condition     = length(setsubtract(toset(keys(var.scc_sources)), var.workflow_organisations)) == 0
      error_message = "Every SCC source must map to an authorised Uumi organisation."
    }

    precondition {
      condition = (
        length(setsubtract(var.secret_sources, var.workflow_organisations)) == 0 &&
        length(setsubtract(
          toset([for source in values(var.provider_sources) : source.organisation_id]),
          var.workflow_organisations,
        )) == 0 &&
        length(setsubtract(
          toset([for schedule in values(var.rotation_schedules) : schedule.organisation_id]),
          var.workflow_organisations,
        )) == 0
      )
      error_message = "Every ingestion source must map to an authorised Uumi organisation."
    }
  }
}

module "runtime" {
  source = "../../modules/runtime"

  project_id                   = var.project_id
  region                       = var.region
  api_service_account          = module.identity.emails["uumi-api"]
  web_service_account          = module.identity.emails["uumi-web"]
  ingestion_service_account    = module.identity.emails["uumi-ingestion"]
  publisher_service_account    = module.identity.emails["uumi-publisher"]
  broker_service_account       = module.identity.emails["uumi-broker"]
  coordinator_service_account  = module.identity.emails["uumi-coordinator"]
  notification_service_account = module.identity.emails["uumi-notification"]
  auditlog_service_account     = module.identity.emails["uumi-auditlog"]
  api_member                   = module.identity.members["uumi-api"]
  web_member                   = module.identity.members["uumi-web"]
  coordinator_member           = module.identity.members["uumi-coordinator"]
  workflow_member              = module.identity.members["uumi-workflow"]
  event_member                 = module.identity.members["uumi-events"]
  scc_push_service_account     = module.identity.emails["uumi-events"]
  oidc_audience                = var.oidc_audience
  github_app_slug              = var.github_app_slug == null ? "" : var.github_app_slug
  github_client_id             = var.github_client_id == null ? "" : var.github_client_id
  github_client_secret_version = var.github_client_secret_version == null ? "" : var.github_client_secret_version
  github_callback_url          = var.github_callback_url == null ? "" : var.github_callback_url
  google_cloud_client_id       = var.google_cloud_client_id == null ? "" : var.google_cloud_client_id
  google_cloud_client_secret_version = (
    var.google_cloud_client_secret_version == null ? "" : var.google_cloud_client_secret_version
  )
  google_cloud_callback_url = var.google_cloud_callback_url == null ? "" : var.google_cloud_callback_url
  github_webhook_secret_version = (
    var.github_webhook_secret_version == null ? "" : var.github_webhook_secret_version
  )
  api_image            = var.api_image
  web_image            = var.web_image
  ingestion_image      = var.ingestion_image
  publisher_image      = var.publisher_image
  broker_image         = var.broker_image
  coordinator_image    = var.coordinator_image
  notification_image   = var.notification_image
  auditlog_image       = var.auditlog_image
  notification_app_url = var.notification_app_url
  notification_email_secret_version = (
    var.notification_email_secret_version == null ? "" : var.notification_email_secret_version
  )
  notification_email_sender = (var.notification_email_sender == null ? "" : var.notification_email_sender)
  browser_image             = var.browser_image
  browser_gateway_url       = coalesce(module.gateway.url, "https://browser-gateway.disabled.invalid")
  evidence_bucket           = module.storage.evidence_bucket
  walkthrough_bucket        = module.storage.walkthrough_bucket
  capability_secret_version = local.capability_secret_version
  capability_public_key     = var.capability_public_key
  browser_template          = module.browser.template
  browser_zone              = var.zone
  model_armor_template      = "projects/${local.agent_project_id}/locations/${var.region}/templates/uumi-agent-guardrails"
  network                   = module.browser.network
  subnetwork                = module.browser.runtime_subnetwork

  depends_on = [module.project, module.storage, module.browser, module.gateway]
}

data "google_project" "current" {
  project_id = var.project_id
}

data "google_project" "agent" {
  provider = google.agent

  project_id = local.agent_project_id
}

module "perimeter" {
  count  = var.access_policy_id == null || var.operator_access_level == null ? 0 : 1
  source = "../../modules/perimeter"

  providers = {
    google = google.org
  }

  project_id            = var.project_id
  project_number        = data.google_project.current.number
  organisation_id       = data.google_project.current.org_id
  access_policy_id      = var.access_policy_id
  operator_access_level = var.operator_access_level
  region                = var.region
  enable_agent_gateway  = var.enable_gateway

  depends_on = [module.project]
}

locals {
  legacy_agent_trust_domain = data.google_project.current.org_id != null && data.google_project.current.org_id != "" ? (
    "agents.global.org-${data.google_project.current.org_id}.system.id.goog"
    ) : (
    "agents.global.project-${data.google_project.current.number}.system.id.goog"
  )
  legacy_agent_principal_set = "principalSet://${local.legacy_agent_trust_domain}/attribute.platformContainer/aiplatform/projects/${data.google_project.current.number}"
  agent_trust_domain = data.google_project.agent.org_id != null && data.google_project.agent.org_id != "" ? (
    "agents.global.org-${data.google_project.agent.org_id}.system.id.goog"
    ) : (
    "agents.global.project-${data.google_project.agent.number}.system.id.goog"
  )
  agent_principal_set = "principalSet://${local.agent_trust_domain}/attribute.platformContainer/aiplatform/projects/${data.google_project.agent.number}"
}

module "governance" {
  count = var.enable_gateway && (
    !local.split_agent_project || var.enable_legacy_gateway
  ) ? 1 : 0
  source = "../../modules/governance"

  project_id          = var.project_id
  region              = var.region
  agent_principal_set = local.legacy_agent_principal_set
  deployment_member   = module.identity.members["uumi-agents"]
  model_armor_callers = toset([
    module.identity.members["uumi-api"],
    module.identity.members["uumi-coordinator"],
  ])
  broker_uri = module.runtime.broker_uri

  depends_on = [module.project]
}

module "agentstorage" {
  count  = local.split_agent_project ? 1 : 0
  source = "../../modules/agentstorage"

  providers = {
    google      = google.agent
    google-beta = google-beta.agent
  }

  project_id        = local.agent_project_id
  project_number    = data.google_project.agent.number
  location          = var.region
  deployment_member = module.identity.members["uumi-agents"]

  depends_on = [module.agent_project]
}

module "agent_governance" {
  count  = local.split_agent_project ? 1 : 0
  source = "../../modules/governance"

  providers = {
    google      = google.agent
    google-beta = google-beta.agent
  }

  project_id          = local.agent_project_id
  region              = var.region
  agent_principal_set = local.agent_principal_set
  deployment_member   = module.identity.members["uumi-agents"]
  model_armor_callers = toset([
    module.identity.members["uumi-api"],
    module.identity.members["uumi-coordinator"],
  ])
  broker_uri = var.agent_broker_uri

  depends_on = [module.agent_project, module.agentstorage]
}

module "gateway" {
  source = "../../modules/gateway"

  project_id            = var.project_id
  region                = var.region
  image                 = var.gateway_image
  service_account       = module.identity.emails["uumi-gateway"]
  capability_public_key = var.capability_public_key
  network               = module.browser.network
  subnetwork            = module.browser.runtime_subnetwork
  users                 = var.gateway_users

  depends_on = [module.project, module.browser, module.storage]
}

module "events" {
  source = "../../modules/events"

  project_id              = var.project_id
  region                  = var.region
  publisher_member        = module.identity.members["uumi-publisher"]
  event_member            = module.identity.members["uumi-events"]
  event_service_account   = module.identity.emails["uumi-events"]
  secretmanager_member    = module.storage.secretmanager_member
  publisher_enabled       = var.publisher_image != null
  ingestion_enabled       = var.ingestion_image != null
  notification_enabled    = var.notification_image != null && var.notification_app_url != null
  auditlog_enabled        = var.auditlog_image != null
  publisher_name          = module.runtime.publisher_name
  publisher_uri           = module.runtime.publisher_uri
  api_uri                 = module.runtime.api_uri
  reaper_service_account  = module.identity.emails["uumi-workflow"]
  reaper_organisations    = var.workflow_organisations
  ingestion_uri           = module.runtime.ingestion_uri
  notification_name       = module.runtime.notification_name
  notification_uri        = module.runtime.notification_uri
  auditlog_name           = module.runtime.auditlog_name
  auditlog_uri            = module.runtime.auditlog_uri
  oidc_audience           = var.oidc_audience
  scc_sources             = var.scc_sources
  secret_sources          = var.secret_sources
  rotation_schedules      = var.rotation_schedules
  detection_organisations = var.workflow_organisations

  depends_on = [module.project, module.runtime]
}

module "workflow" {
  source = "../../modules/workflow"

  project_id            = var.project_id
  region                = var.region
  service_account       = module.identity.emails["uumi-workflow"]
  event_service_account = module.identity.emails["uumi-events"]
  event_topic           = module.events.topic
  enabled = (
    var.api_image != null &&
    var.coordinator_image != null &&
    var.browser_image != null &&
    var.broker_image != null
  )
  api_url         = module.runtime.api_uri
  coordinator_url = module.runtime.coordinator_uri
  oidc_audience   = var.oidc_audience

  depends_on = [module.project, module.events, module.runtime]
}

resource "google_project_iam_member" "browser_runtime" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["uumi-browser"]
}

resource "google_project_iam_member" "api_runtime" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["uumi-api"]
}

resource "google_project_iam_member" "coordinator_runtime" {
  for_each = toset([
    "roles/logging.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.viewer",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["uumi-coordinator"]
}

resource "google_project_iam_member" "agent_deployer" {
  for_each = toset([
    "roles/agentregistry.viewer",
    "roles/aiplatform.user",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["uumi-agents"]
}

resource "google_project_iam_member" "agent_project_deployer" {
  provider = google.agent
  for_each = local.split_agent_project ? toset([
    "roles/agentregistry.viewer",
    "roles/aiplatform.user",
    "roles/serviceusage.serviceUsageConsumer",
  ]) : toset([])

  project = local.agent_project_id
  role    = each.value
  member  = module.identity.members["uumi-agents"]
}

locals {
  agent_context_grants = {
    api_memory          = [module.identity.members["uumi-api"], "roles/aiplatform.memoryUser"]
    api_session         = [module.identity.members["uumi-api"], "roles/aiplatform.sessionUser"]
    coordinator_memory  = [module.identity.members["uumi-coordinator"], "roles/aiplatform.memoryViewer"]
    coordinator_session = [module.identity.members["uumi-coordinator"], "roles/aiplatform.sessionUser"]
  }
}

resource "google_project_iam_member" "agent_context" {
  for_each = local.agent_context_grants

  project = var.project_id
  member  = each.value[0]
  role    = each.value[1]
}

resource "google_project_iam_member" "agent_project_context" {
  provider = google.agent
  for_each = local.split_agent_project ? local.agent_context_grants : {}

  project = local.agent_project_id
  member  = each.value[0]
  role    = each.value[1]
}

resource "google_project_iam_member" "ingestion_runtime" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["uumi-ingestion"]
}

resource "google_project_iam_member" "auditlog_runtime" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = module.identity.members["uumi-auditlog"]
}

locals {
  telemetry_runtime_roles = {
    broker       = module.identity.members["uumi-broker"]
    gateway      = module.identity.members["uumi-gateway"]
    notification = module.identity.members["uumi-notification"]
    publisher    = module.identity.members["uumi-publisher"]
    web          = module.identity.members["uumi-web"]
  }
  telemetry_runtime_grants = merge([
    for account, member in local.telemetry_runtime_roles : {
      for role in [
        "roles/logging.logWriter",
        "roles/monitoring.metricWriter",
        "roles/cloudtrace.agent",
        ] : "${account}-${replace(role, "/", "-")}" => {
        member = member
        role   = role
      }
    }
  ]...)
}

resource "google_project_iam_member" "telemetry_runtime" {
  for_each = local.telemetry_runtime_grants

  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}
