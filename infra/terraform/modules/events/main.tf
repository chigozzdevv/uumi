locals {
  publisher = var.publisher_name == null || var.publisher_uri == null ? {} : {
    publisher = {
      name = var.publisher_name
      uri  = var.publisher_uri
    }
  }
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
