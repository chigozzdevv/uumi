locals {
  restricted_services = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "containeranalysis.googleapis.com",
    "eventarc.googleapis.com",
    "firestore.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "securitycenter.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "telemetry.googleapis.com",
    "videointelligence.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ])
}

resource "google_access_context_manager_service_perimeter" "firekey" {
  parent         = "accessPolicies/${var.access_policy_id}"
  name           = "accessPolicies/${var.access_policy_id}/servicePerimeters/firekey"
  title          = "FireKey"
  description    = "Enforced data-exfiltration boundary for FireKey managed services."
  perimeter_type = "PERIMETER_TYPE_REGULAR"

  status {
    resources           = ["projects/${var.project_number}"]
    restricted_services = local.restricted_services
    access_levels       = [var.operator_access_level]

    vpc_accessible_services {
      enable_restriction = true
      allowed_services = [
        "RESTRICTED-SERVICES",
        "run.googleapis.com",
      ]
    }
  }

  deletion_policy = "PREVENT"
}

resource "google_org_policy_policy" "locations" {
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    inherit_from_parent = false

    rules {
      values {
        allowed_values = ["in:${var.region}-locations"]
      }
    }
  }
}
