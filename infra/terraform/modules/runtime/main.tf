locals {
  api = var.api_image == null ? {} : { api = var.api_image }
}

resource "google_artifact_registry_repository" "runtime" {
  project         = var.project_id
  location        = var.region
  repository_id   = "firekey"
  description     = "Immutable FireKey runtime images."
  format          = "DOCKER"
  deletion_policy = "PREVENT"

  docker_config {
    immutable_tags = true
  }

  vulnerability_scanning_config {
    enablement_config = "INHERITED"
  }
}

resource "google_cloud_run_v2_service" "api" {
  for_each = local.api

  project             = var.project_id
  location            = var.region
  name                = "firekey-api"
  description         = "Private FireKey control-plane API."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  custom_audiences    = [var.oidc_audience]

  template {
    service_account                  = var.api_service_account
    timeout                          = "60s"
    max_instance_request_concurrency = 40
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    containers {
      name  = "api"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "FIREKEY_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "FIREKEY_FIRESTORE_DATABASE"
        value = "(default)"
      }

      env {
        name  = "FIREKEY_OIDC_AUDIENCE"
        value = var.oidc_audience
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 2
        failure_threshold     = 15

        http_get {
          path = "/health/live"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 10
        timeout_seconds       = 2
        period_seconds        = 10
        failure_threshold     = 3

        http_get {
          path = "/health/live"
          port = 8080
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository.runtime]
}

resource "google_cloud_run_v2_service_iam_member" "workflow" {
  for_each = google_cloud_run_v2_service.api

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.workflow_member
}
