locals {
  enabled = var.api_url != null && var.coordinator_url != null ? { rotation = true } : {}
}

resource "google_workflows_workflow" "rotation" {
  for_each = local.enabled

  project             = var.project_id
  region              = var.region
  name                = "uumi-rotation"
  description         = "Authoritative Uumi twelve-stage credential rotation state machine."
  service_account     = var.service_account
  deletion_protection = true
  source_contents     = file("${path.root}/../../../workflows/rotation.yaml")

  user_env_vars = {
    UUMI_API_URL         = var.api_url
    UUMI_COORDINATOR_URL = var.coordinator_url
    UUMI_OIDC_AUDIENCE   = var.oidc_audience
  }
}

resource "google_project_iam_member" "event_receiver" {
  for_each = local.enabled

  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${var.event_service_account}"
}

resource "google_project_iam_member" "workflow_invoker" {
  for_each = local.enabled

  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${var.event_service_account}"
}

resource "google_eventarc_trigger" "rotation" {
  for_each = google_workflows_workflow.rotation

  project                 = var.project_id
  location                = var.region
  name                    = "uumi-run-events"
  service_account         = var.event_service_account
  deletion_policy         = "PREVENT"
  event_data_content_type = "application/json"

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = var.event_topic
    }
  }

  destination {
    workflow = each.value.id
  }

  depends_on = [
    google_project_iam_member.event_receiver,
    google_project_iam_member.workflow_invoker,
  ]
}
