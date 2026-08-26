locals {
  data_services = toset([
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
    # Google VPC Service Controls supports Workflows but not the separate executions API.
    "workflows.googleapis.com",
  ])
  # Agent Gateway and VPC Service Controls cannot both govern Agent Runtime. Keep
  # persisted data services inside the perimeter when Gateway governance is enabled.
  restricted_services = var.enable_agent_gateway ? local.data_services : setunion(
    local.data_services,
    toset(["aiplatform.googleapis.com"]),
  )
}

resource "google_access_context_manager_service_perimeter" "uumi" {
  parent         = "accessPolicies/${var.access_policy_id}"
  name           = "accessPolicies/${var.access_policy_id}/servicePerimeters/uumi"
  title          = "Uumi"
  description    = "Enforced data-exfiltration boundary for Uumi managed services."
  perimeter_type = "PERIMETER_TYPE_REGULAR"

  status {
    resources           = ["projects/${var.project_number}"]
    restricted_services = local.restricted_services
    access_levels       = [var.operator_access_level]

    ingress_policies {
      title = "agent-runtime-build"

      ingress_from {
        identities = [
          "serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
        ]
        sources {
          access_level = "*"
        }
      }

      ingress_to {
        resources = ["projects/${var.project_number}"]

        operations {
          service_name = "storage.googleapis.com"
          method_selectors {
            method = "google.storage.objects.get"
          }
          method_selectors {
            method = "google.storage.objects.list"
          }
          method_selectors {
            method = "google.storage.buckets.getStorageLayout"
          }
        }

        operations {
          service_name = "artifactregistry.googleapis.com"
          method_selectors {
            method = "artifactregistry.googleapis.com/DockerRead"
          }
          method_selectors {
            method = "artifactregistry.googleapis.com/DockerWrite"
          }
        }
      }
    }

    ingress_policies {
      title = "agent-runtime-logging"

      ingress_from {
        identities = [
          "serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
        ]
        sources {
          access_level = "*"
        }
      }

      ingress_to {
        resources = ["projects/${var.project_number}"]

        operations {
          service_name = "logging.googleapis.com"
          method_selectors {
            method = "LoggingServiceV2.WriteLogEntries"
          }
        }
      }
    }

    vpc_accessible_services {
      enable_restriction = true
      allowed_services = concat(
        [
          "RESTRICTED-SERVICES",
          "run.googleapis.com",
        ],
        var.enable_agent_gateway ? [
          "aiplatform.googleapis.com",
          # Managed Agent Runtime refreshes its location-bound credentials before data API calls.
          "iamcredentials.googleapis.com",
        ] : [],
      )
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
