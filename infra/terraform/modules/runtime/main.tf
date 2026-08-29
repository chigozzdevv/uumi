locals {
  api       = var.api_image == null ? {} : { api = var.api_image }
  web       = var.web_image == null || var.api_image == null ? {} : { web = var.web_image }
  publisher = var.publisher_image == null ? {} : { publisher = var.publisher_image }
  broker    = var.broker_image == null ? {} : { broker = var.broker_image }
  ingestion = var.ingestion_image == null ? {} : { ingestion = var.ingestion_image }
  notification = var.notification_image == null || var.notification_app_url == null ? {} : {
    notification = var.notification_image
  }
  auditlog = var.auditlog_image == null ? {} : { auditlog = var.auditlog_image }
  coordinator = (
    var.coordinator_image == null || var.browser_image == null || var.broker_image == null
    ? {}
    : { coordinator = var.coordinator_image }
  )
}

resource "google_cloud_run_v2_service" "auditlog" {
  for_each = local.auditlog

  project             = var.project_id
  location            = var.region
  name                = "uumi-auditlog"
  description         = "Private canonical audit log publisher."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  custom_audiences    = [var.oidc_audience]

  template {
    service_account                  = var.auditlog_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 1
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "auditlog"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "UUMI_FIRESTORE_DATABASE"
        value = "(default)"
      }

      env {
        name  = "UUMI_REGION"
        value = var.region
      }
      env {
        name  = "UUMI_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name  = "UUMI_TRUSTED_PUSH_SERVICE_ACCOUNT"
        value = var.scc_push_service_account
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
        timeout_seconds   = 2
        period_seconds    = 2
        failure_threshold = 15
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

resource "google_cloud_run_v2_service_iam_member" "auditlog_invoker" {
  for_each = google_cloud_run_v2_service.auditlog

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.event_member
}

resource "google_cloud_run_v2_service" "notification" {
  for_each = local.notification

  project             = var.project_id
  location            = var.region
  name                = "uumi-notification"
  description         = "Private durable multi-channel notification worker."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  custom_audiences    = [var.oidc_audience]

  template {
    service_account                  = var.notification_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 20
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "notification"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "UUMI_FIRESTORE_DATABASE"
        value = "(default)"
      }
      env {
        name  = "UUMI_REGION"
        value = var.region
      }
      env {
        name  = "UUMI_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name  = "UUMI_TRUSTED_PUSH_SERVICE_ACCOUNT"
        value = var.scc_push_service_account
      }
      env {
        name  = "UUMI_APP_URL"
        value = var.notification_app_url
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
        timeout_seconds   = 2
        period_seconds    = 2
        failure_threshold = 15
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

resource "google_cloud_run_v2_service_iam_member" "notification_invoker" {
  for_each = google_cloud_run_v2_service.notification

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.event_member
}

resource "google_cloud_run_v2_service" "ingestion" {
  for_each = local.ingestion

  project             = var.project_id
  location            = var.region
  name                = "uumi-ingestion"
  description         = "Authenticated GitHub and Security Command Center incident intake."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"
  custom_audiences    = [var.oidc_audience]

  template {
    service_account                  = var.ingestion_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 40
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "ingestion"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "UUMI_FIRESTORE_DATABASE"
        value = "(default)"
      }
      env {
        name  = "UUMI_REGION"
        value = var.region
      }
      env {
        name  = "UUMI_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name  = "UUMI_SCC_PUSH_SERVICE_ACCOUNT"
        value = var.scc_push_service_account
      }
      env {
        name  = "UUMI_GITHUB_WEBHOOK_SECRET"
        value = var.github_webhook_secret_version
      }
      env {
        name  = "UUMI_TRUSTED_PUSH_SERVICE_ACCOUNTS"
        value = jsonencode([var.scc_push_service_account])
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

resource "google_cloud_run_v2_service_iam_member" "ingestion" {
  for_each = google_cloud_run_v2_service.ingestion

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_artifact_registry_repository" "runtime" {
  project         = var.project_id
  location        = var.region
  repository_id   = "uumi"
  description     = "Immutable Uumi runtime images."
  format          = "DOCKER"
  deletion_policy = "PREVENT"

  docker_config {
    immutable_tags = true
  }

  vulnerability_scanning_config {
    enablement_config = "INHERITED"
  }
}

resource "google_cloud_run_v2_service" "web" {
  for_each = local.web

  project             = var.project_id
  location            = var.region
  name                = "uumi-web"
  description         = "Public transport boundary for authenticated Uumi API requests."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = var.web_service_account
    timeout                          = "55s"
    max_instance_request_concurrency = 80
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "web"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "UUMI_REGION"
        value = var.region
      }

      env {
        name  = "UUMI_API_URL"
        value = google_cloud_run_v2_service.api["api"].uri
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
        timeout_seconds   = 2
        period_seconds    = 2
        failure_threshold = 15

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

  depends_on = [google_cloud_run_v2_service.api]
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  for_each = google_cloud_run_v2_service.web

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "web_api" {
  for_each = google_cloud_run_v2_service.api

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.web_member
}

resource "google_cloud_run_v2_service" "api" {
  for_each = local.api

  project             = var.project_id
  location            = var.region
  name                = "uumi-api"
  description         = "Private Uumi control-plane API."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  custom_audiences    = [var.oidc_audience]

  template {
    service_account                  = var.api_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 40
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "api"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "UUMI_FIRESTORE_DATABASE"
        value = "(default)"
      }

      env {
        name  = "UUMI_REGION"
        value = var.region
      }

      env {
        name  = "UUMI_OIDC_AUDIENCE"
        value = var.oidc_audience
      }

      env {
        name  = "UUMI_CAPABILITY_SECRET"
        value = var.capability_secret_version
      }

      env {
        name  = "UUMI_BROKER_URL"
        value = try(google_cloud_run_v2_service.broker["broker"].uri, "")
      }

      env {
        name  = "UUMI_BROKER_SERVICE_ACCOUNT"
        value = var.broker_service_account
      }

      env {
        name  = "UUMI_BROWSER_GATEWAY_URL"
        value = var.browser_gateway_url
      }

      env {
        name  = "UUMI_BROWSER_SETUP_URL"
        value = var.browser_setup_url
      }

      env {
        name  = "UUMI_BROWSER_ZONE"
        value = var.browser_zone
      }

      env {
        name  = "UUMI_BROWSER_TEMPLATE"
        value = var.browser_template
      }

      env {
        name  = "UUMI_BROWSER_WORKER_IMAGE"
        value = var.browser_image == null ? "" : var.browser_image
      }

      env {
        name  = "UUMI_MODEL_ARMOR_TEMPLATE"
        value = var.model_armor_template
      }

      env {
        name  = "UUMI_MODEL_ARMOR_RESPONSE_TEMPLATE"
        value = var.model_armor_response_template
      }

      env {
        name  = "UUMI_CAPABILITY_PUBLIC_KEY"
        value = var.capability_public_key
      }

      env {
        name  = "UUMI_EVIDENCE_BUCKET"
        value = var.evidence_bucket
      }

      env {
        name  = "UUMI_WALKTHROUGH_BUCKET"
        value = var.walkthrough_bucket
      }

      env {
        name  = "UUMI_GITHUB_APP_SLUG"
        value = var.github_app_slug
      }

      env {
        name  = "UUMI_GITHUB_CLIENT_ID"
        value = var.github_client_id
      }

      env {
        name  = "UUMI_GITHUB_CLIENT_SECRET"
        value = var.github_client_secret_version
      }

      env {
        name  = "UUMI_GITHUB_CALLBACK_URL"
        value = var.github_callback_url
      }

      env {
        name  = "UUMI_GOOGLE_CLOUD_CLIENT_ID"
        value = var.google_cloud_client_id
      }

      env {
        name  = "UUMI_GOOGLE_CLOUD_CLIENT_SECRET"
        value = var.google_cloud_client_secret_version
      }

      env {
        name  = "UUMI_GOOGLE_CLOUD_CALLBACK_URL"
        value = var.google_cloud_callback_url
      }

      env {
        name  = "UUMI_NOTIFICATION_EMAIL_SECRET_VERSION"
        value = var.notification_email_secret_version
      }

      env {
        name  = "UUMI_NOTIFICATION_EMAIL_SENDER"
        value = var.notification_email_sender
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

resource "google_cloud_run_v2_service" "publisher" {
  for_each = local.publisher

  project             = var.project_id
  location            = var.region
  name                = "uumi-publisher"
  description         = "Private Uumi transactional outbox publisher."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account                  = var.publisher_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 1
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "publisher"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "UUMI_FIRESTORE_DATABASE"
        value = "(default)"
      }

      env {
        name  = "UUMI_REGION"
        value = var.region
      }

      env {
        name  = "UUMI_EVENT_TOPIC"
        value = var.event_topic
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

resource "google_cloud_run_v2_service_iam_member" "event_invoker" {
  for_each = google_cloud_run_v2_service.publisher

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.event_member
}

resource "google_cloud_run_v2_service" "broker" {
  for_each = local.broker

  project             = var.project_id
  location            = var.region
  name                = "uumi-broker"
  description         = "Private capability-scoped MCP Tool Broker."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account                  = var.broker_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 20
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 30
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "broker"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "UUMI_REGION"
        value = var.region
      }
      env {
        name  = "UUMI_EVIDENCE_BUCKET"
        value = var.evidence_bucket
      }
      env {
        name  = "UUMI_CAPABILITY_PUBLIC_KEY"
        value = var.capability_public_key
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 3
        period_seconds        = 3
        failure_threshold     = 20
        http_get {
          path = "/health/live"
          port = 8080
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "coordinator_broker" {
  for_each = google_cloud_run_v2_service.broker

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.coordinator_member
}

resource "google_cloud_run_v2_service_iam_member" "api_broker" {
  for_each = google_cloud_run_v2_service.broker

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.api_member
}

resource "google_cloud_run_v2_service" "coordinator" {
  for_each = local.coordinator

  project             = var.project_id
  location            = var.region
  name                = "uumi-coordinator"
  description         = "Private deterministic stage execution service."
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  custom_audiences    = [var.oidc_audience]

  template {
    service_account                  = var.coordinator_service_account
    timeout                          = "900s"
    max_instance_request_concurrency = 4
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network
        subnetwork = var.subnetwork
        tags       = ["uumi-runtime"]
      }
    }

    containers {
      name  = "coordinator"
      image = each.value

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "UUMI_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "UUMI_REGION"
        value = var.region
      }
      env {
        name  = "UUMI_ZONE"
        value = var.browser_zone
      }
      env {
        name  = "UUMI_EVIDENCE_BUCKET"
        value = var.evidence_bucket
      }
      env {
        name  = "UUMI_CAPABILITY_SECRET"
        value = var.capability_secret_version
      }
      env {
        name  = "UUMI_BROWSER_TEMPLATE"
        value = var.browser_template
      }
      env {
        name  = "UUMI_BROWSER_IMAGE"
        value = var.browser_image
      }
      env {
        name  = "UUMI_MODEL_ARMOR_TEMPLATE"
        value = var.model_armor_template
      }
      env {
        name  = "UUMI_MODEL_ARMOR_RESPONSE_TEMPLATE"
        value = var.model_armor_response_template
      }
      env {
        name  = "UUMI_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name  = "UUMI_BROKER_URL"
        value = try(google_cloud_run_v2_service.broker["broker"].uri, "")
      }
      resources {
        limits = {
          cpu    = "4"
          memory = "4Gi"
        }
        cpu_idle          = false
        startup_cpu_boost = true
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 3
        period_seconds        = 3
        failure_threshold     = 30
        http_get {
          path = "/health/live"
          port = 8080
        }
      }
    }
  }

  depends_on = [google_cloud_run_v2_service.broker]
}

resource "google_cloud_run_v2_service_iam_member" "workflow_coordinator" {
  for_each = google_cloud_run_v2_service.coordinator

  project  = each.value.project
  location = each.value.location
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = var.workflow_member
}
