locals {
  publisher = !var.publisher_enabled ? {} : {
    publisher = {
      name = var.publisher_name
      uri  = var.publisher_uri
    }
  }
  scc       = !var.ingestion_enabled ? {} : var.scc_sources
  secrets   = !var.ingestion_enabled ? toset([]) : var.secret_sources
  schedules = !var.ingestion_enabled ? {} : var.rotation_schedules
  detection = !var.ingestion_enabled ? toset([]) : var.detection_organisations
  reapers = {
    for organisation_id in var.reaper_organisations : organisation_id => {
      api_uri         = var.api_uri
      service_account = var.reaper_service_account
    }
  }
  notification = !var.notification_enabled ? {} : {
    notification = { name = var.notification_name, uri = var.notification_uri }
  }
  auditlog = !var.auditlog_enabled ? {} : {
    auditlog = { name = var.auditlog_name, uri = var.auditlog_uri }
  }
  push = length(local.scc) + length(local.secrets) + length(local.notification) + length(local.auditlog) == 0 ? [] : ["enabled"]
}

resource "google_project_service_identity" "pubsub" {
  provider = google-beta
  project  = var.project_id
  service  = "pubsub.googleapis.com"
}

resource "google_pubsub_topic" "events" {
  project                    = var.project_id
  name                       = "uumi-events"
  message_retention_duration = "604800s"
  deletion_policy            = "PREVENT"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
    enforce_in_transit          = true
  }
}

resource "google_pubsub_topic_iam_member" "publisher" {
  project = google_pubsub_topic.events.project
  topic   = google_pubsub_topic.events.name
  role    = "roles/pubsub.publisher"
  member  = var.publisher_member
}

resource "google_pubsub_topic" "notification_deadletter" {
  for_each = local.notification

  project                    = var.project_id
  name                       = "uumi-notification-deadletter"
  message_retention_duration = "2678400s"
  deletion_policy            = "PREVENT"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
    enforce_in_transit          = true
  }
}

resource "google_pubsub_topic" "audit_deadletter" {
  for_each = local.auditlog

  project                    = var.project_id
  name                       = "uumi-audit-deadletter"
  message_retention_duration = "2678400s"
  deletion_policy            = "PREVENT"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
    enforce_in_transit          = true
  }
}

resource "google_pubsub_subscription" "audit" {
  for_each = local.auditlog

  project                      = var.project_id
  name                         = "uumi-audit-events"
  topic                        = google_pubsub_topic.events.id
  ack_deadline_seconds         = 60
  message_retention_duration   = "604800s"
  retain_acked_messages        = false
  enable_message_ordering      = true
  enable_exactly_once_delivery = false
  deletion_policy              = "PREVENT"

  expiration_policy {
    ttl = ""
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.audit_deadletter[each.key].id
    max_delivery_attempts = 10
  }
  push_config {
    push_endpoint = "${each.value.uri}/events"
    attributes = {
      x-goog-version = "v1"
    }
    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_pubsub_topic_iam_member" "audit_deadletter" {
  for_each = google_pubsub_topic.audit_deadletter

  project = each.value.project
  topic   = each.value.name
  role    = "roles/pubsub.publisher"
  member  = google_project_service_identity.pubsub.member
}

resource "google_pubsub_subscription_iam_member" "audit_deadletter" {
  for_each = google_pubsub_subscription.audit

  project      = each.value.project
  subscription = each.value.name
  role         = "roles/pubsub.subscriber"
  member       = google_project_service_identity.pubsub.member
}

resource "google_pubsub_subscription" "audit_deadletter" {
  for_each = google_pubsub_topic.audit_deadletter

  project                    = var.project_id
  name                       = "uumi-audit-deadletter-review"
  topic                      = each.value.id
  ack_deadline_seconds       = 60
  message_retention_duration = "2678400s"
  retain_acked_messages      = true
  deletion_policy            = "PREVENT"

  expiration_policy {
    ttl = ""
  }
}

resource "google_monitoring_alert_policy" "audit_deadletter" {
  for_each = google_pubsub_subscription.audit_deadletter

  project      = var.project_id
  display_name = "Uumi canonical audit dead letter"
  combiner     = "OR"

  conditions {
    display_name = "Undelivered canonical audit dead letters"
    condition_threshold {
      filter          = "resource.type = \"pubsub_subscription\" AND resource.labels.subscription_id = \"${each.value.name}\" AND metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  alert_strategy {
    auto_close = "604800s"
  }
}

resource "google_pubsub_subscription" "notification" {
  for_each = local.notification

  project                      = var.project_id
  name                         = "uumi-notification-events"
  topic                        = google_pubsub_topic.events.id
  ack_deadline_seconds         = 60
  message_retention_duration   = "604800s"
  retain_acked_messages        = false
  enable_message_ordering      = true
  enable_exactly_once_delivery = false
  deletion_policy              = "PREVENT"

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.notification_deadletter[each.key].id
    max_delivery_attempts = 10
  }

  push_config {
    push_endpoint = "${each.value.uri}/events"

    attributes = {
      x-goog-version = "v1"
    }

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_pubsub_topic_iam_member" "notification_deadletter" {
  for_each = google_pubsub_topic.notification_deadletter

  project = each.value.project
  topic   = each.value.name
  role    = "roles/pubsub.publisher"
  member  = google_project_service_identity.pubsub.member
}

resource "google_pubsub_subscription_iam_member" "notification_deadletter" {
  for_each = google_pubsub_subscription.notification

  project      = each.value.project
  subscription = each.value.name
  role         = "roles/pubsub.subscriber"
  member       = google_project_service_identity.pubsub.member
}

resource "google_pubsub_subscription" "notification_deadletter" {
  for_each = google_pubsub_topic.notification_deadletter

  project                    = var.project_id
  name                       = "uumi-notification-deadletter-review"
  topic                      = each.value.id
  ack_deadline_seconds       = 60
  message_retention_duration = "2678400s"
  retain_acked_messages      = true
  deletion_policy            = "PREVENT"

  expiration_policy {
    ttl = ""
  }
}

resource "google_monitoring_alert_policy" "notification_deadletter" {
  for_each = google_pubsub_subscription.notification_deadletter

  project      = var.project_id
  display_name = "Uumi notification delivery dead letter"
  combiner     = "OR"

  conditions {
    display_name = "Undelivered notification dead letters"
    condition_threshold {
      filter          = "resource.type = \"pubsub_subscription\" AND resource.labels.subscription_id = \"${each.value.name}\" AND metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  alert_strategy {
    auto_close = "604800s"
  }
}

resource "google_pubsub_topic" "scc" {
  for_each = local.scc

  project                    = var.project_id
  name                       = "uumi-scc-${replace(each.key, "_", "-")}"
  message_retention_duration = "604800s"
  deletion_policy            = "PREVENT"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
    enforce_in_transit          = true
  }
}

resource "google_pubsub_topic" "secrets" {
  for_each = local.secrets

  project                    = var.project_id
  name                       = "uumi-secrets-${replace(each.value, "_", "-")}"
  message_retention_duration = "604800s"
  deletion_policy            = "PREVENT"
}

resource "google_pubsub_topic_iam_member" "secrets" {
  for_each = google_pubsub_topic.secrets

  project = each.value.project
  topic   = each.value.name
  role    = "roles/pubsub.publisher"
  member  = var.secretmanager_member
}

resource "google_scc_v2_organization_notification_config" "uumi" {
  for_each = local.scc

  config_id    = "uumi-${replace(each.key, "_", "-")}"
  organization = each.value.cloud_organisation_id
  location     = each.value.location
  description  = "Uumi credential exposure findings for ${each.key}."
  pubsub_topic = google_pubsub_topic.scc[each.key].id

  streaming_config {
    filter = each.value.filter
  }
}

resource "google_pubsub_topic" "deadletter" {
  count = length(local.push)

  project                    = var.project_id
  name                       = "uumi-ingestion-deadletter"
  message_retention_duration = "2678400s"
  deletion_policy            = "PREVENT"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
    enforce_in_transit          = true
  }
}

resource "google_pubsub_subscription" "scc" {
  for_each = local.scc

  project                      = var.project_id
  name                         = "uumi-scc-${replace(each.key, "_", "-")}-push"
  topic                        = google_pubsub_topic.scc[each.key].id
  ack_deadline_seconds         = 60
  message_retention_duration   = "604800s"
  retain_acked_messages        = false
  enable_exactly_once_delivery = false
  deletion_policy              = "PREVENT"

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.deadletter[0].id
    max_delivery_attempts = 10
  }

  push_config {
    push_endpoint = "${var.ingestion_uri}/v1/scc/${each.key}"

    attributes = {
      x-goog-version = "v1"
    }

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }

  depends_on = [google_scc_v2_organization_notification_config.uumi]
}

resource "google_pubsub_subscription" "secrets" {
  for_each = local.secrets

  project                      = var.project_id
  name                         = "uumi-secrets-${replace(each.value, "_", "-")}-push"
  topic                        = google_pubsub_topic.secrets[each.value].id
  ack_deadline_seconds         = 60
  message_retention_duration   = "604800s"
  retain_acked_messages        = false
  enable_exactly_once_delivery = false
  deletion_policy              = "PREVENT"

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.deadletter[0].id
    max_delivery_attempts = 10
  }

  push_config {
    push_endpoint = "${var.ingestion_uri}/v1/secrets/${each.value}"

    attributes = {
      x-goog-version = "v1"
    }

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_pubsub_subscription" "deadletter" {
  count = length(local.push)

  project                    = var.project_id
  name                       = "uumi-ingestion-deadletter-review"
  topic                      = google_pubsub_topic.deadletter[0].id
  ack_deadline_seconds       = 60
  message_retention_duration = "2678400s"
  retain_acked_messages      = true
  deletion_policy            = "PREVENT"

  expiration_policy {
    ttl = ""
  }
}

resource "google_monitoring_alert_policy" "ingestion_deadletter" {
  count = length(local.push)

  project      = var.project_id
  display_name = "Uumi incident ingestion dead letter"
  combiner     = "OR"

  conditions {
    display_name = "Undelivered ingestion dead letters"
    condition_threshold {
      filter          = "resource.type = \"pubsub_subscription\" AND resource.labels.subscription_id = \"${google_pubsub_subscription.deadletter[0].name}\" AND metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  alert_strategy {
    auto_close = "604800s"
  }
}

resource "google_pubsub_topic_iam_member" "deadletter" {
  count = length(local.push)

  project = google_pubsub_topic.deadletter[0].project
  topic   = google_pubsub_topic.deadletter[0].name
  role    = "roles/pubsub.publisher"
  member  = google_project_service_identity.pubsub.member
}

resource "google_pubsub_subscription_iam_member" "deadletter" {
  for_each = google_pubsub_subscription.scc

  project      = each.value.project
  subscription = each.value.name
  role         = "roles/pubsub.subscriber"
  member       = google_project_service_identity.pubsub.member
}

resource "google_pubsub_subscription_iam_member" "secret_deadletter" {
  for_each = google_pubsub_subscription.secrets

  project      = each.value.project
  subscription = each.value.name
  role         = "roles/pubsub.subscriber"
  member       = google_project_service_identity.pubsub.member
}

resource "google_service_account_iam_member" "push_token" {
  count = length(local.push)

  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.event_service_account}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_project_service_identity.pubsub.member
}

resource "google_cloud_scheduler_job" "rotation" {
  for_each = local.schedules

  project          = var.project_id
  region           = var.region
  name             = "uumi-rotation-${replace(each.key, "_", "-")}"
  description      = "Starts policy-controlled rotation for ${each.value.credential_id}."
  schedule         = each.value.schedule
  time_zone        = each.value.time_zone
  attempt_deadline = "60s"
  deletion_policy  = "PREVENT"

  retry_config {
    retry_count          = 5
    min_backoff_duration = "10s"
    max_backoff_duration = "600s"
    max_doublings        = 5
  }

  http_target {
    http_method = "POST"
    uri         = "${var.ingestion_uri}/v1/schedules/${each.value.organisation_id}/${each.key}"
    body        = base64encode(jsonencode({ credential_id = each.value.credential_id }))
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_cloud_scheduler_job" "detection" {
  for_each = local.detection

  project          = var.project_id
  region           = var.region
  name             = "uumi-detect-${replace(each.value, "_", "-")}"
  description      = "Detects credential expiry, provider drift, and runtime misalignment."
  schedule         = "*/15 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  deletion_policy  = "PREVENT"

  retry_config {
    retry_count          = 5
    min_backoff_duration = "10s"
    max_backoff_duration = "600s"
    max_doublings        = 5
  }

  http_target {
    http_method = "POST"
    uri         = "${var.ingestion_uri}/v1/detect/${each.value}"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_cloud_scheduler_job" "run_reaper" {
  for_each = local.reapers

  project          = var.project_id
  region           = var.region
  name             = "uumi-run-reaper-${replace(each.key, "_", "-")}"
  description      = "Recovers expired run leases and interrupted cleanup transitions."
  schedule         = "* * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  deletion_policy  = "PREVENT"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${each.value.api_uri}/v1/organisations/${each.key}/runs/reap"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = each.value.service_account
      audience              = var.oidc_audience
    }
  }

  lifecycle {
    precondition {
      condition = (
        each.value.api_uri != null &&
        each.value.service_account != null
      )
      error_message = "Run reapers require an API URI and service account."
    }
  }
}

resource "google_project_iam_member" "event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = var.event_member
}

resource "google_eventarc_trigger" "outbox" {
  for_each = local.publisher

  project                 = var.project_id
  location                = var.region
  name                    = "uumi-outbox-created"
  service_account         = var.event_service_account
  deletion_policy         = "PREVENT"
  event_data_content_type = "application/protobuf"

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.firestore.document.v1.created"
  }

  matching_criteria {
    attribute = "database"
    value     = "(default)"
  }

  matching_criteria {
    attribute = "namespace"
    value     = "(default)"
  }

  matching_criteria {
    attribute = "document"
    operator  = "match-path-pattern"
    value     = "organisations/{organisation}/outbox/{event}"
  }

  destination {
    cloud_run_service {
      service = each.value.name
      region  = var.region
      path    = "/publish"
    }
  }

  depends_on = [google_project_iam_member.event_receiver]
}

resource "google_eventarc_trigger" "notification" {
  for_each = local.notification

  project                 = var.project_id
  location                = var.region
  name                    = "uumi-notification-created"
  service_account         = var.event_service_account
  deletion_policy         = "PREVENT"
  event_data_content_type = "application/protobuf"

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.firestore.document.v1.created"
  }
  matching_criteria {
    attribute = "database"
    value     = "(default)"
  }
  matching_criteria {
    attribute = "namespace"
    value     = "(default)"
  }
  matching_criteria {
    attribute = "document"
    operator  = "match-path-pattern"
    value     = "organisations/{organisation}/notifications/{notification}/notification-deliveries/{delivery}"
  }

  destination {
    cloud_run_service {
      service = each.value.name
      region  = var.region
      path    = "/drain"
    }
  }

  depends_on = [google_project_iam_member.event_receiver]
}

resource "google_eventarc_trigger" "audit" {
  for_each = local.auditlog

  project                 = var.project_id
  location                = var.region
  name                    = "uumi-audit-created"
  service_account         = var.event_service_account
  deletion_policy         = "PREVENT"
  event_data_content_type = "application/protobuf"

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.firestore.document.v1.created"
  }
  matching_criteria {
    attribute = "database"
    value     = "(default)"
  }
  matching_criteria {
    attribute = "namespace"
    value     = "(default)"
  }
  matching_criteria {
    attribute = "document"
    operator  = "match-path-pattern"
    value     = "organisations/{organisation}/audit-outbox/{event}"
  }

  destination {
    cloud_run_service {
      service = each.value.name
      region  = var.region
      path    = "/drain"
    }
  }

  depends_on = [google_project_iam_member.event_receiver]
}

resource "google_cloud_scheduler_job" "audit" {
  for_each = local.auditlog

  project          = var.project_id
  region           = var.region
  name             = "uumi-audit-sweep"
  description      = "Recovers pending canonical audit writes and expired delivery leases."
  schedule         = "* * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  deletion_policy  = "PREVENT"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${each.value.uri}/drain"

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_cloud_scheduler_job" "notification" {
  for_each = local.notification

  project          = var.project_id
  region           = var.region
  name             = "uumi-notification-sweep"
  description      = "Recovers pending notification deliveries and expired worker leases."
  schedule         = "* * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  deletion_policy  = "PREVENT"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${each.value.uri}/drain"

    oidc_token {
      service_account_email = var.event_service_account
      audience              = var.oidc_audience
    }
  }
}

resource "google_cloud_scheduler_job" "outbox" {
  for_each = local.publisher

  project          = var.project_id
  region           = var.region
  name             = "uumi-outbox-sweep"
  description      = "Recovers unpublished Uumi events and expired delivery leases."
  schedule         = "* * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  deletion_policy  = "PREVENT"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${each.value.uri}/publish"

    oidc_token {
      service_account_email = var.event_service_account
      audience              = each.value.uri
    }
  }
}
