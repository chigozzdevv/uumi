resource "google_firestore_database" "primary" {
  project                           = var.project_id
  name                              = "(default)"
  location_id                       = var.location
  type                              = "FIRESTORE_NATIVE"
  database_edition                  = "STANDARD"
  concurrency_mode                  = "PESSIMISTIC"
  app_engine_integration_mode       = "DISABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  deletion_policy                   = "PREVENT"
}

resource "google_project_iam_member" "database_user" {
  for_each = var.users

  project = var.project_id
  role    = "roles/datastore.user"
  member  = each.value

  condition {
    title       = "firekey-${each.key}-database"
    description = "Restricts FireKey data access to its primary database."
    expression  = "resource.name == '${google_firestore_database.primary.id}'"
  }
}

resource "google_firestore_document" "principal" {
  for_each = var.principals

  project     = var.project_id
  database    = google_firestore_database.primary.name
  collection  = "organisations/${each.value.organisation_id}/principals"
  document_id = sha256(each.value.subject)
  fields = jsonencode({
    subject = {
      stringValue = each.value.subject
    }
    roles = {
      arrayValue = {
        values = [
          for role in sort(tolist(each.value.roles)) : {
            stringValue = role
          }
        ]
      }
    }
    enabled = {
      booleanValue = true
    }
  })
}

resource "google_firestore_index" "outbox" {
  project         = var.project_id
  database        = google_firestore_database.primary.name
  collection      = "outbox"
  query_scope     = "COLLECTION_GROUP"
  deletion_policy = "PREVENT"

  fields {
    field_path = "published_at"
    order      = "ASCENDING"
  }

  fields {
    field_path = "available_at"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "notification_delivery" {
  project         = var.project_id
  database        = google_firestore_database.primary.name
  collection      = "notification-deliveries"
  query_scope     = "COLLECTION_GROUP"
  deletion_policy = "PREVENT"

  fields {
    field_path = "state"
    order      = "ASCENDING"
  }

  fields {
    field_path = "available_at"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "audit_outbox" {
  project         = var.project_id
  database        = google_firestore_database.primary.name
  collection      = "audit-outbox"
  query_scope     = "COLLECTION_GROUP"
  deletion_policy = "PREVENT"

  fields {
    field_path = "logged_at"
    order      = "ASCENDING"
  }

  fields {
    field_path = "available_at"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "active_approvals" {
  project         = var.project_id
  database        = google_firestore_database.primary.name
  collection      = "approvals"
  query_scope     = "COLLECTION_GROUP"
  deletion_policy = "PREVENT"

  fields {
    field_path = "decision"
    order      = "ASCENDING"
  }

  fields {
    field_path = "expires_at"
    order      = "ASCENDING"
  }
}

resource "google_logging_project_bucket_config" "audit" {
  project        = var.project_id
  location       = var.location
  bucket_id      = "firekey-audit"
  description    = "Canonical FireKey hash-chained audit events."
  retention_days = 2555
  locked         = true
  cmek_settings {
    kms_key_name = google_kms_crypto_key.evidence.id
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_logging_project_sink" "audit" {
  project                = var.project_id
  name                   = "firekey-audit"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.audit.id}"
  filter                 = "logName=\"projects/${var.project_id}/logs/firekey-audit\""
  unique_writer_identity = true
}

resource "google_project_iam_member" "audit_sink" {
  project = var.project_id
  role    = "roles/logging.bucketWriter"
  member  = google_logging_project_sink.audit.writer_identity
}

resource "google_kms_key_ring" "firekey" {
  project  = var.project_id
  name     = "firekey"
  location = var.location
}

resource "google_kms_crypto_key" "evidence" {
  name            = "firekey-evidence"
  key_ring        = google_kms_key_ring.firekey.id
  rotation_period = "7776000s"
  purpose         = "ENCRYPT_DECRYPT"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_service_identity" "storage" {
  provider = google-beta
  project  = var.project_id
  service  = "storage.googleapis.com"
}

resource "google_project_service_identity" "secretmanager" {
  provider = google-beta
  project  = var.project_id
  service  = "secretmanager.googleapis.com"
}

resource "google_project_service_identity" "aiplatform" {
  provider = google-beta
  project  = var.project_id
  service  = "aiplatform.googleapis.com"
}

resource "google_project_service_identity" "video" {
  provider = google-beta
  project  = var.project_id
  service  = "videointelligence.googleapis.com"
}

resource "google_project_service_identity" "logging" {
  provider = google-beta
  project  = var.project_id
  service  = "logging.googleapis.com"
}

resource "google_kms_crypto_key_iam_member" "service_crypto" {
  for_each = {
    storage       = google_project_service_identity.storage.member
    secretmanager = google_project_service_identity.secretmanager.member
    aiplatform    = google_project_service_identity.aiplatform.member
    video         = google_project_service_identity.video.member
    logging       = google_project_service_identity.logging.member
  }

  crypto_key_id = google_kms_crypto_key.evidence.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}

resource "google_storage_bucket" "evidence" {
  project                     = var.project_id
  name                        = "${var.project_id}-firekey-evidence"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  storage_class               = "STANDARD"

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = 31536000
    is_locked        = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence.id
  }

  lifecycle_rule {
    condition {
      age = 2555
    }
    action {
      type          = "SetStorageClass"
      storage_class = "ARCHIVE"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket" "agents" {
  project                     = var.project_id
  name                        = "${var.project_id}-firekey-agents"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence.id
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket" "walkthroughs" {
  project                     = var.project_id
  name                        = "${var.project_id}-firekey-walkthroughs"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  storage_class               = "STANDARD"

  dynamic "cors" {
    for_each = length(var.walkthrough_cors_origins) == 0 ? [] : [var.walkthrough_cors_origins]
    content {
      origin          = cors.value
      method          = ["PUT"]
      response_header = ["Content-Type", "Content-Range", "Location"]
      max_age_seconds = 3600
    }
  }

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = 86400
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.evidence.id
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "walkthrough_create" {
  count = var.walkthrough_user == null ? 0 : 1

  bucket = google_storage_bucket.walkthroughs.name
  role   = "roles/storage.objectCreator"
  member = var.walkthrough_user
}

resource "google_storage_bucket_iam_member" "walkthrough_view" {
  for_each = merge(
    var.walkthrough_user == null ? {} : { api = var.walkthrough_user },
    { video = google_project_service_identity.video.member },
  )

  bucket = google_storage_bucket.walkthroughs.name
  role   = "roles/storage.objectViewer"
  member = each.value
}

resource "google_kms_crypto_key_iam_member" "walkthrough_crypto" {
  for_each = var.walkthrough_user == null ? {} : { api = var.walkthrough_user }

  crypto_key_id = google_kms_crypto_key.evidence.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}

resource "google_storage_bucket_iam_member" "agents" {
  count = var.agent_staging_user == null ? 0 : 1

  bucket = google_storage_bucket.agents.name
  role   = "roles/storage.objectUser"
  member = var.agent_staging_user
}

resource "google_kms_crypto_key_iam_member" "agents" {
  count = var.agent_staging_user == null ? 0 : 1

  crypto_key_id = google_kms_crypto_key.evidence.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = var.agent_staging_user
}

resource "google_storage_bucket_iam_member" "evidence_writer" {
  for_each = var.evidence_users

  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectUser"
  member = each.value
}

resource "google_kms_crypto_key_iam_member" "evidence_crypto" {
  for_each = var.evidence_users

  crypto_key_id = google_kms_crypto_key.evidence.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}

resource "google_secret_manager_secret" "capability" {
  project   = var.project_id
  secret_id = "firekey-capability"

  replication {
    user_managed {
      replicas {
        location = var.location
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.evidence.id
        }
      }
    }
  }

  deletion_protection = true
}

resource "google_secret_manager_secret_iam_member" "capability" {
  for_each = var.secret_accessors

  project   = google_secret_manager_secret.capability.project
  secret_id = google_secret_manager_secret.capability.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

resource "google_secret_manager_secret" "browser_session" {
  for_each = var.browser_session_organisations

  project   = var.project_id
  secret_id = "firekey-browser-session-${each.value}"

  replication {
    user_managed {
      replicas {
        location = var.location
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.evidence.id
        }
      }
    }
  }

  deletion_protection = true
}

resource "google_secret_manager_secret_iam_member" "browser_session_add" {
  for_each = var.browser_session_user == null ? {} : google_secret_manager_secret.browser_session

  project   = each.value.project
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = var.browser_session_user
}

resource "google_secret_manager_secret_iam_member" "browser_session_read" {
  for_each = var.browser_session_user == null ? {} : google_secret_manager_secret.browser_session

  project   = each.value.project
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.browser_session_user
}

resource "google_secret_manager_secret_iam_member" "browser_session_manage" {
  for_each = var.browser_session_manager == null ? {} : google_secret_manager_secret.browser_session

  project   = each.value.project
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = var.browser_session_manager
}

resource "google_secret_manager_secret" "github_webhook" {
  project   = var.project_id
  secret_id = "firekey-github-webhook"

  replication {
    user_managed {
      replicas {
        location = var.location
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.evidence.id
        }
      }
    }
  }

  deletion_protection = true
}

resource "google_secret_manager_secret_iam_member" "github_webhook" {
  count = var.github_webhook_accessor == null ? 0 : 1

  project   = google_secret_manager_secret.github_webhook.project
  secret_id = google_secret_manager_secret.github_webhook.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.github_webhook_accessor
}

resource "google_secret_manager_secret" "github_oauth" {
  project   = var.project_id
  secret_id = "firekey-github-oauth-client"

  replication {
    user_managed {
      replicas {
        location = var.location
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.evidence.id
        }
      }
    }
  }

  deletion_protection = true
}

resource "google_secret_manager_secret_iam_member" "github_oauth" {
  count = var.github_oauth_accessor == null ? 0 : 1

  project   = google_secret_manager_secret.github_oauth.project
  secret_id = google_secret_manager_secret.github_oauth.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.github_oauth_accessor
}

resource "google_secret_manager_secret" "provider" {
  for_each = var.provider_sources

  project   = var.project_id
  secret_id = "firekey-provider-webhook-${each.value.organisation_id}-${each.value.provider}"

  replication {
    user_managed {
      replicas {
        location = var.location
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.evidence.id
        }
      }
    }
  }

  deletion_protection = true
}

resource "google_secret_manager_secret_iam_member" "provider" {
  for_each = var.provider_secret_accessor == null ? {} : google_secret_manager_secret.provider

  project   = each.value.project
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.provider_secret_accessor
}
