project_id                = "replace-with-project-id"
region                    = "us-east1"
enable_gateway            = true
access_policy_id          = "123456789012"
operator_access_level     = "accessPolicies/123456789012/accessLevels/firekeyOperators"
browser_allowed_domains   = ["console.vendor.example"]
runtime_connector_domains = ["api.vendor.example"]
gateway_users             = ["group:firekey-operators@example.com"]

identity_platform_domains = ["usefirekey.web.app", "usefirekey.firebaseapp.com"]

workflow_organisations             = ["org_replace"]
api_image                          = null
publisher_image                    = null
ingestion_image                    = null
broker_image                       = null
coordinator_image                  = null
browser_image                      = null
gateway_image                      = null
notification_image                 = null
auditlog_image                     = null
notification_app_url               = null
# Immutable Secret Manager version containing FireKey's Resend API key.
notification_email_secret_version  = null
notification_email_sender          = null
github_app_slug                    = null
github_client_id                   = null
github_client_secret_version       = null
github_callback_url                = null
google_cloud_client_id             = null
google_cloud_client_secret_version = null
google_cloud_callback_url          = null
github_webhook_secret_version      = null
capability_secret_version          = null
capability_public_key              = "replace-with-ed25519-public-key-base64url-x"

notification_secrets = {
  email = {
    project_id = "replace-with-project-id"
    secret_id  = "firekey-notification-resend"
  }
}

scc_sources = {
  org_replace = {
    cloud_organisation_id = "123456789012"
    location              = "global"
    filter                = "state = \"ACTIVE\" AND severity = \"CRITICAL\""
  }
}

secret_sources = ["org_replace"]

provider_sources = {
  vendor = {
    organisation_id = "org_replace"
    provider        = "vendor"
  }
}

rotation_schedules = {
  production_service = {
    organisation_id = "org_replace"
    credential_id   = "credential_replace"
    schedule        = "0 2 * * 0"
    time_zone       = "Etc/UTC"
  }
}
