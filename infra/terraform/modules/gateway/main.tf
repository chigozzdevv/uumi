data "google_project" "current" {
  project_id = var.project_id
}

locals {
  enabled = var.image == null ? {} : { gateway = true }
}

resource "google_cloud_run_v2_service" "gateway" {
  for_each = local.enabled

  project             = var.project_id
  location            = var.region
  name                = "firekey-browser-gateway"
  description         = "IAP-authenticated live browser view and takeover gateway."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = true

  template {
    service_account                  = var.service_account
    timeout                          = "3600s"
    max_instance_request_concurrency = 40
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["firekey-gateway"]
      }
    }

    containers {
      name  = "gateway"
      image = var.image

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "FIREKEY_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "FIREKEY_REGION"
        value = var.region
      }
      env {
        name  = "FIREKEY_IAP_AUDIENCE"
        value = "/projects/${data.google_project.current.number}/locations/${var.region}/services/firekey-browser-gateway"
      }
      env {
        name  = "FIREKEY_CAPABILITY_PUBLIC_KEY"
        value = var.capability_public_key
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
    }
  }
}

resource "google_project_service_identity" "iap" {
  for_each = local.enabled
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"
}

resource "google_cloud_run_v2_service_iam_member" "iap" {
  for_each = local.enabled

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway[each.key].name
  role     = "roles/run.invoker"
  member   = google_project_service_identity.iap[each.key].member
}

resource "google_iap_web_cloud_run_service_iam_member" "user" {
  for_each = local.enabled == {} ? toset([]) : var.users

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.gateway["gateway"].name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}
