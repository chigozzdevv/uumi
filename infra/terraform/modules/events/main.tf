locals {
  publisher = var.publisher_name == null || var.publisher_uri == null ? {} : {
    publisher = {
      name = var.publisher_name
      uri  = var.publisher_uri
    }
  }
  scc = var.ingestion_uri == null ? {} : var.scc_sources
}

resource "google_project_service_identity" "pubsub" {
  provider = google-beta
  project  = var.project_id
  service  = "pubsub.googleapis.com"
}

resource "google_pubsub_topic" "events" {
  project                    = var.project_id
  name                       = "firekey-events"
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

resource "google_pubsub_topic" "scc" {
  for_each = local.scc

  project                    = var.project_id
  name                       = "firekey-scc-${replace(each.key, "_", "-")}"
  message_retention_duration = "604800s"
  deletion_policy            = "PREVENT"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
    enforce_in_transit          = true
  }
}

resource "google_scc_v2_organization_notification_config" "firekey" {
  for_each = local.scc

  config_id    = "firekey-${replace(each.key, "_", "-")}"
  organization = each.value.cloud_organisation_id
  location     = each.value.location
  description  = "FireKey credential exposure findings for ${each.key}."
  pubsub_topic = google_pubsub_topic.scc[each.key].id

  streaming_config {
    filter = each.value.filter
  }
}

resource "google_pubsub_topic" "deadletter" {
  count = length(local.scc) == 0 ? 0 : 1

  project                    = var.project_id
  name                       = "firekey-scc-deadletter"
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
  name                         = "firekey-scc-${replace(each.key, "_", "-")}-push"
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

  depends_on = [google_scc_v2_organization_notification_config.firekey]
}

resource "google_pubsub_subscription" "deadletter" {
  count = length(local.scc) == 0 ? 0 : 1

  project                    = var.project_id
  name                       = "firekey-scc-deadletter-review"
  topic                      = google_pubsub_topic.deadletter[0].id
  ack_deadline_seconds       = 60
  message_retention_duration = "2678400s"
  retain_acked_messages      = true
  deletion_policy            = "PREVENT"

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic_iam_member" "deadletter" {
  count = length(local.scc) == 0 ? 0 : 1

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

resource "google_service_account_iam_member" "push_token" {
  count = length(local.scc) == 0 ? 0 : 1

  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.event_service_account}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_project_service_identity.pubsub.member
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
  name                    = "firekey-outbox-created"
  service_account         = var.event_service_account
  deletion_policy         = "PREVENT"
  event_data_content_type = "application/json"

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

resource "google_cloud_scheduler_job" "outbox" {
  for_each = local.publisher

  project          = var.project_id
  region           = var.region
  name             = "firekey-outbox-sweep"
  description      = "Recovers unpublished FireKey events and expired delivery leases."
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
