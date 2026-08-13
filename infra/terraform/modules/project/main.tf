locals {
  base_services = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "eventarc.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ])

  gateway_services = toset([
    "agentregistry.googleapis.com",
    "apphub.googleapis.com",
    "apptopology.googleapis.com",
    "cloudapiregistry.googleapis.com",
    "dataform.googleapis.com",
    "discoveryengine.googleapis.com",
    "dns.googleapis.com",
    "modelarmor.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "notebooks.googleapis.com",
    "observability.googleapis.com",
    "telemetry.googleapis.com",
    "texttospeech.googleapis.com",
  ])

  services = var.enable_gateway ? setunion(local.base_services, local.gateway_services) : local.base_services
}

resource "google_project_service" "service" {
  for_each = local.services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
