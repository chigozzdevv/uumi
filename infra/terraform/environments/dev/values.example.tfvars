project_id     = "replace-with-project-id"
region         = "us-east1"
enable_gateway = true

workflow_organisations    = ["org_replace"]
api_image                 = null
publisher_image           = null
ingestion_image           = null
broker_image              = null
coordinator_image         = null
browser_image             = null
gateway_image             = null
capability_secret_version = null

scc_sources = {
  org_replace = {
    cloud_organisation_id = "123456789012"
    location              = "global"
    filter                = "state = \"ACTIVE\" AND severity = \"CRITICAL\""
  }
}
