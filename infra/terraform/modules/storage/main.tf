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
