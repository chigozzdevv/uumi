locals {
  registry = "//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.region}"
  broker_host = (
    var.broker_uri == null ? null : split("/", trimprefix(var.broker_uri, "https://"))[0]
  )
  endpoints = merge(
    {
      aiplatform = {
        display_name = "Vertex AI regional API"
        url          = "https://${var.region}-aiplatform.googleapis.com"
      }
      aiplatform_mtls = {
        display_name = "Vertex AI regional mTLS API"
        url          = "https://${var.region}-aiplatform.mtls.googleapis.com"
      }
      aiplatform_rep = {
        display_name = "Vertex AI regional REP API"
        url          = "https://aiplatform.${var.region}.rep.googleapis.com"
      }
      aiplatform_model = {
        display_name = "Vertex AI US multi-region model API"
        url          = "https://aiplatform.us.rep.googleapis.com"
      }
      agentregistry = {
        display_name = "Agent Registry API"
        url          = "https://agentregistry.googleapis.com"
      }
      aiplatform_global = {
        display_name = "Vertex AI global API"
        url          = "https://aiplatform.googleapis.com"
      }
      cloudresourcemanager_mtls = {
        display_name = "Resource Manager mTLS API"
        url          = "https://cloudresourcemanager.mtls.googleapis.com"
      }
      firestore = {
        display_name = "Firestore API"
        url          = "https://firestore.googleapis.com"
      }
      iamcredentials = {
        display_name = "IAM Service Account Credentials API"
        url          = "https://iamcredentials.googleapis.com"
      }
      iamcredentials_mtls = {
        display_name = "IAM Service Account Credentials mTLS API"
        url          = "https://iamcredentials.mtls.googleapis.com"
      }
      logging = {
        display_name = "Cloud Logging API"
        url          = "https://logging.googleapis.com"
      }
      monitoring = {
        display_name = "Cloud Monitoring API"
        url          = "https://monitoring.googleapis.com"
      }
      telemetry = {
        display_name = "Cloud Telemetry API"
        url          = "https://telemetry.googleapis.com"
      }
      telemetry_mtls = {
        display_name = "Cloud Telemetry mTLS API"
        url          = "https://telemetry.mtls.googleapis.com"
      }
    },
  )
}

resource "google_model_armor_template" "uumi" {
  project         = var.project_id
  location        = var.region
  template_id     = "uumi-agent-guardrails"
  deletion_policy = "ENABLED"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }

    rai_settings {
      dynamic "rai_filters" {
        for_each = toset(["DANGEROUS", "HARASSMENT", "HATE_SPEECH", "SEXUALLY_EXPLICIT"])
        content {
          filter_type      = rai_filters.value
          confidence_level = "MEDIUM_AND_ABOVE"
        }
      }
    }
  }

  template_metadata {
    enforcement_type                         = "INSPECT_AND_BLOCK"
    ignore_partial_invocation_failures       = false
    log_sanitize_operations                  = true
    log_template_operations                  = true
    custom_prompt_safety_error_code          = 403
    custom_prompt_safety_error_message       = "Uumi agent input was blocked by policy."
    custom_llm_response_safety_error_code    = 403
    custom_llm_response_safety_error_message = "Uumi agent output was blocked by policy."

    filter_version_selector {
      alias = "FILTER_VERSION_ALIAS_STABLE"
    }

    multi_language_detection {
      enable_multi_language_detection = true
    }
  }
}

resource "google_network_services_agent_gateway" "ingress" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-agent-ingress"
  description     = "Model Armor governed client access to Uumi Agent Runtime."
  deletion_policy = "PREVENT"

  google_managed {
    governed_access_path = "CLIENT_TO_AGENT"
  }
}

resource "google_network_services_agent_gateway" "egress" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-agent-egress"
  description     = "Default-deny governed egress from Uumi Agent Runtime."
  registries      = [local.registry]
  deletion_policy = "PREVENT"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }
}

resource "google_network_services_authz_extension" "armor" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-model-armor"
  description     = "Fail-closed content screening for Uumi agent traffic."
  service         = "modelarmor.${var.region}.rep.googleapis.com"
  timeout         = "1s"
  fail_open       = false
  deletion_policy = "PREVENT"
  metadata = {
    model_armor_settings = jsonencode([{
      request_template_id  = google_model_armor_template.uumi.name
      response_template_id = google_model_armor_template.uumi.name
    }])
  }
}

resource "google_network_services_authz_extension" "iap" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-iap"
  description     = "Fail-closed identity authorization for Uumi agent egress."
  service         = "iap.googleapis.com"
  timeout         = "1s"
  fail_open       = false
  deletion_policy = "PREVENT"
  metadata = {
    iapPolicyVersion = "V1"
  }
}

resource "google_network_security_authz_policy" "ingress" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-agent-ingress-armor"
  description     = "Screens Uumi agent prompts and responses."
  policy_profile  = "CONTENT_AUTHZ"
  action          = "CUSTOM"
  deletion_policy = "PREVENT"

  target {
    resources = [google_network_services_agent_gateway.ingress.id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.armor.id]
    }
  }
}

resource "google_network_security_authz_policy" "egress" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-agent-egress-armor"
  description     = "Screens supported Uumi MCP egress without intercepting internal data APIs."
  policy_profile  = "CONTENT_AUTHZ"
  action          = "CUSTOM"
  deletion_policy = "PREVENT"

  target {
    resources = [google_network_services_agent_gateway.egress.id]
  }

  dynamic "http_rules" {
    for_each = local.broker_host == null ? [] : [local.broker_host]
    content {
      to {
        operations {
          hosts {
            exact = http_rules.value
          }

          paths {
            prefix = "/mcp"
          }
        }
      }

      when = "request.headers['content-type'] == 'application/json' || request.headers['content-type'].startsWith('text/')"
    }
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.armor.id]
    }
  }
}

resource "google_network_security_authz_policy" "egress_identity" {
  project         = var.project_id
  location        = var.region
  name            = "uumi-agent-egress-identity"
  description     = "Enforces Agent Identity and IAP policy on Uumi agent egress."
  policy_profile  = "REQUEST_AUTHZ"
  action          = "CUSTOM"
  deletion_policy = "PREVENT"

  target {
    resources = [google_network_services_agent_gateway.egress.id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.iap.id]
    }
  }
}

resource "google_agent_registry_service" "endpoint" {
  for_each = local.endpoints

  project         = var.project_id
  location        = var.region
  service_id      = "uumi-${replace(each.key, "_", "-")}"
  display_name    = each.value.display_name
  description     = "Approved Uumi Agent Gateway destination."
  deletion_policy = "PREVENT"

  interfaces {
    url              = each.value.url
    protocol_binding = "HTTP_JSON"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

resource "google_agent_registry_service" "broker" {
  count = var.broker_uri == null ? 0 : 1

  project         = var.project_id
  location        = var.region
  service_id      = "uumi-broker"
  display_name    = "Uumi MCP broker"
  description     = "Capability-scoped Uumi provider and runtime tools."
  deletion_policy = "PREVENT"

  interfaces {
    url              = "${trimsuffix(var.broker_uri, "/")}/mcp"
    protocol_binding = "JSONRPC"
  }

  mcp_server_spec {
    type = "NO_SPEC"
  }
}

resource "google_iap_agent_registry_endpoint_iam_member" "egress" {
  for_each = google_agent_registry_service.endpoint

  project     = var.project_id
  location    = var.region
  endpoint_id = element(reverse(split("/", each.value.registry_resource)), 0)
  role        = "roles/iap.egressor"
  member      = var.agent_principal_set
}

resource "google_iap_agent_registry_mcp_server_iam_member" "broker" {
  count = var.broker_uri == null ? 0 : 1

  project       = var.project_id
  location      = var.region
  mcp_server_id = element(reverse(split("/", google_agent_registry_service.broker[0].registry_resource)), 0)
  role          = "roles/iap.egressor"
  member        = var.agent_principal_set
}

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  modelarmor_grants = {
    for grant in setproduct(
      toset(["gateway", "runtime"]),
      toset([
        "roles/modelarmor.calloutUser",
        "roles/modelarmor.user",
        "roles/serviceusage.serviceUsageConsumer",
      ]),
      ) : "${grant[0]}-${grant[1]}" => {
      role = grant[1]
      member = (
        grant[0] == "gateway" ?
        "serviceAccount:service-${data.google_project.current.number}@gcp-sa-dep.iam.gserviceaccount.com" :
        "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
      )
    }
  }
}

resource "google_project_iam_member" "modelarmor" {
  for_each = local.modelarmor_grants

  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}

resource "google_project_iam_custom_role" "gateway_user" {
  project     = var.project_id
  role_id     = "uumiAgentGatewayUser"
  title       = "Uumi Agent Gateway User"
  description = "Allows the Vertex AI service agent to attach approved Agent Gateways."
  permissions = [
    "networkservices.agentGateways.get",
    "networkservices.agentGateways.use",
    "networkservices.operations.get",
  ]
}

resource "google_project_iam_member" "gateway_user" {
  project = var.project_id
  role    = google_project_iam_custom_role.gateway_user.name
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "agent" {
  for_each = toset([
    "roles/agentregistry.viewer",
    "roles/aiplatform.agentDefaultAccess",
    "roles/aiplatform.expressUser",
    "roles/aiplatform.user",
    "roles/browser",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/serviceusage.serviceUsageConsumer",
  ])

  project = var.project_id
  role    = each.value
  member  = var.agent_principal_set
}

resource "google_project_iam_member" "agent_database" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = var.agent_principal_set

  condition {
    title       = "uumi-managed-agents-database"
    description = "Restricts managed Agent Identity task persistence to Uumi's primary database."
    expression  = "resource.name == 'projects/${var.project_id}/databases/(default)'"
  }
}

resource "google_project_iam_custom_role" "caller" {
  project     = var.project_id
  role_id     = "uumiAgentCaller"
  title       = "Uumi Agent Caller"
  description = "Queries managed Uumi agent deployments."
  permissions = [
    "aiplatform.reasoningEngines.get",
    "aiplatform.reasoningEngines.query",
  ]
}

resource "google_project_iam_custom_role" "deployer" {
  project     = var.project_id
  role_id     = "uumiAgentDeployer"
  title       = "Uumi Agent IAM Deployer"
  description = "Applies approved caller bindings to managed Uumi agent deployments."
  permissions = [
    "aiplatform.reasoningEngines.getIamPolicy",
    "aiplatform.reasoningEngines.setIamPolicy",
  ]
}

resource "google_project_iam_member" "deployer" {
  project = var.project_id
  role    = google_project_iam_custom_role.deployer.name
  member  = var.deployment_member
}
