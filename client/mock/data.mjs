const now = "2026-08-16T18:00:00Z"
const earlier = "2026-08-01T09:00:00Z"
const hash = (character) => character.repeat(64)

const providerConnection = (id, platform, displayName) => ({
  id,
  organisation_id: "org_acme",
  platform,
  display_name: displayName,
  roles: ["provider"],
  interface: "api",
  authorization: "api-key",
  authorization_reference: `projects/acme-prod/secrets/${platform}-admin/versions/1`,
  capabilities: ["provider.listCredentialMetadata", "provider.createCredential", "provider.getCredentialStatus", "provider.revokeCredential"],
  allowed_resources: [`${platform}:credentials:*`],
  http: {
    base_url: `https://api.${platform}.example`,
    auth: { scheme: "bearer", header: "Authorization", prefix: "Bearer " },
    list_credentials: { method: "GET", path: "/credentials", success_statuses: [200], query: {}, body: {}, list_items: "items", provider_id_field: "id", secret_field: null, name_field: "name" },
    create_credential: { method: "POST", path: "/credentials", success_statuses: [201], query: {}, body: {}, list_items: null, provider_id_field: "id", secret_field: "secret", name_field: "name" },
    revoke_credential: { method: "DELETE", path: "/credentials/{provider_id}", success_statuses: [204], query: {}, body: {}, list_items: null, provider_id_field: null, secret_field: null, name_field: null },
  },
  playbook_id: null,
  playbook_version_id: null,
  status: "ready",
  authenticated_at: earlier,
  authorization_expires_at: null,
  last_validated_at: now,
  region: "us-central1",
  created_at: earlier,
  updated_at: now,
  revision: 1,
})

export const overview = {
  credentials: 8,
  rotations_in_progress: 2,
  failed_rotations: 1,
  open_incidents: 2,
  pending_approvals: 2,
}

export const connections = [
  {
    id: "conn_sendgrid",
    organisation_id: "org_acme",
    platform: "sendgrid",
    display_name: "SendGrid management API",
    roles: ["provider"],
    interface: "api",
    authorization: "api-key",
    authorization_reference: "projects/acme-prod/secrets/sendgrid-admin/versions/3",
    capabilities: ["provider.listCredentialMetadata", "provider.createCredential", "provider.getCredentialStatus", "provider.revokeCredential"],
    allowed_resources: ["sendgrid:credentials:*"],
    http: {
      base_url: "https://api.sendgrid.com/v3",
      auth: { scheme: "bearer", header: "Authorization", prefix: "Bearer " },
      list_credentials: { method: "GET", path: "/api_keys", success_statuses: [200], query: {}, body: {}, list_items: "result", provider_id_field: "api_key_id", secret_field: null, name_field: "name" },
      create_credential: { method: "POST", path: "/api_keys", success_statuses: [201], query: {}, body: { name: "${name}", scopes: "${scopes}" }, list_items: null, provider_id_field: "api_key_id", secret_field: "api_key", name_field: "name" },
      revoke_credential: { method: "DELETE", path: "/api_keys/{provider_id}", success_statuses: [204], query: {}, body: {}, list_items: null, provider_id_field: null, secret_field: null, name_field: null },
    },
    playbook_id: null,
    playbook_version_id: null,
    status: "ready",
    authenticated_at: earlier,
    authorization_expires_at: null,
    last_validated_at: now,
    region: "us-central1",
    created_at: earlier,
    updated_at: now,
    revision: 3,
  },
  {
    id: "conn_secrets",
    organisation_id: "org_acme",
    platform: "google-secret-manager",
    display_name: "Secret Manager · Production",
    roles: ["secret-store"],
    interface: "api",
    authorization: "workload-identity",
    authorization_reference: "workload-identity://firekey-secret-writer",
    capabilities: ["secretStore.createVersion", "secretStore.disableVersion", "secretStore.destroyVersion"],
    allowed_resources: ["projects/acme-prod/secrets"],
    http: null,
    playbook_id: null,
    playbook_version_id: null,
    status: "ready",
    authenticated_at: earlier,
    authorization_expires_at: null,
    last_validated_at: now,
    region: "us-central1",
    created_at: earlier,
    updated_at: now,
    revision: 4,
  },
  {
    id: "conn_runtime",
    organisation_id: "org_acme",
    platform: "cloud-run",
    display_name: "Cloud Run · Production",
    roles: ["runtime"],
    interface: "api",
    authorization: "workload-identity",
    authorization_reference: "workload-identity://firekey-runtime-operator",
    capabilities: ["runtime.deployCandidate", "runtime.shiftTraffic", "runtime.rollback", "runtime.invokeCandidateProbe"],
    allowed_resources: ["projects/acme-prod"],
    http: null,
    playbook_id: null,
    playbook_version_id: null,
    status: "ready",
    authenticated_at: earlier,
    authorization_expires_at: null,
    last_validated_at: now,
    region: "us-central1",
    created_at: earlier,
    updated_at: now,
    revision: 2,
  },
  {
    id: "conn_github",
    organisation_id: "org_acme",
    platform: "github",
    display_name: "GitHub secret scanning",
    roles: ["incident"],
    interface: "api",
    authorization: "oauth",
    authorization_reference: "oauth://github/acme-security",
    capabilities: ["incident.verifyWebhook", "incident.readFinding", "repository.resolveContext"],
    allowed_resources: ["github.com/acme/*"],
    http: null,
    playbook_id: null,
    playbook_version_id: null,
    status: "ready",
    authenticated_at: earlier,
    authorization_expires_at: null,
    last_validated_at: now,
    region: "global",
    created_at: earlier,
    updated_at: now,
    revision: 2,
  },
  {
    id: "conn_telemetry",
    organisation_id: "org_acme",
    platform: "cloud-monitoring",
    display_name: "Cloud Monitoring",
    roles: ["telemetry"],
    interface: "api",
    authorization: "workload-identity",
    authorization_reference: "workload-identity://firekey-verifier",
    capabilities: ["telemetry.queryGeneration", "telemetry.queryAuthFailures"],
    allowed_resources: ["projects/acme-prod"],
    http: null,
    playbook_id: null,
    playbook_version_id: null,
    status: "ready",
    authenticated_at: earlier,
    authorization_expires_at: null,
    last_validated_at: now,
    region: "us-central1",
    created_at: earlier,
    updated_at: now,
    revision: 1,
  },
  {
    id: "conn_vendor",
    organisation_id: "org_acme",
    platform: "internal-vendor",
    display_name: "Vendor administration portal",
    roles: ["provider"],
    interface: "browser",
    authorization: "browser-session",
    authorization_reference: "projects/acme-prod/secrets/vendor-session/versions/6",
    capabilities: ["browser.authenticate", "browser.execute", "browser.secureCapture"],
    allowed_resources: ["admin.vendor.example.com", "login.vendor.example.com"],
    http: null,
    playbook_id: "play_vendor",
    playbook_version_id: "play_vendor_v1",
    status: "reauthentication-required",
    authenticated_at: earlier,
    authorization_expires_at: "2026-08-16T17:12:00Z",
    last_validated_at: "2026-08-16T17:12:00Z",
    region: "us-central1",
    created_at: earlier,
    updated_at: "2026-08-16T17:12:00Z",
    revision: 6,
  },
  providerConnection("conn_stripe", "stripe", "Stripe management API"),
  providerConnection("conn_github_provider", "github", "GitHub credential management API"),
  providerConnection("conn_netsuite", "netsuite", "NetSuite management API"),
  providerConnection("conn_segment", "segment", "Segment management API"),
  providerConnection("conn_snowflake", "snowflake", "Snowflake management API"),
]

export const applications = [
  { id: "app_store", organisation_id: "org_acme", display_name: "Store Platform", repository_ids: ["github:acme/store-api", "github:acme/store-workers"], created_at: earlier, updated_at: now, revision: 2 },
  { id: "app_billing", organisation_id: "org_acme", display_name: "Billing & Subscriptions", repository_ids: ["github:acme/billing"], created_at: earlier, updated_at: now, revision: 1 },
  { id: "app_data", organisation_id: "org_acme", display_name: "Data Platform", repository_ids: ["github:acme/warehouse"], created_at: earlier, updated_at: now, revision: 1 },
]

export const environments = [
  { id: "env_store_prod", organisation_id: "org_acme", application_id: "app_store", display_name: "Production", production: true, region: "us-central1", created_at: earlier, updated_at: now, revision: 2 },
  { id: "env_store_stage", organisation_id: "org_acme", application_id: "app_store", display_name: "Staging", production: false, region: "us-central1", created_at: earlier, updated_at: now, revision: 1 },
  { id: "env_billing_prod", organisation_id: "org_acme", application_id: "app_billing", display_name: "Production", production: true, region: "us-central1", created_at: earlier, updated_at: now, revision: 1 },
  { id: "env_billing_stage", organisation_id: "org_acme", application_id: "app_billing", display_name: "Staging", production: false, region: "us-central1", created_at: earlier, updated_at: now, revision: 1 },
  { id: "env_data_prod", organisation_id: "org_acme", application_id: "app_data", display_name: "Production", production: true, region: "us-east4", created_at: earlier, updated_at: now, revision: 1 },
]

export const services = [
  { id: "svc_notifications", organisation_id: "org_acme", application_id: "app_store", environment_id: "env_store_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-central1/services/notification-worker", display_name: "notification-worker", repository: "github.com/acme/store-workers", identity: "notification-worker@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 5 },
  { id: "svc_checkout", organisation_id: "org_acme", application_id: "app_store", environment_id: "env_store_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-central1/services/checkout-api", display_name: "checkout-api", repository: "github.com/acme/store-api", identity: "checkout-api@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 3 },
  { id: "svc_orders", organisation_id: "org_acme", application_id: "app_store", environment_id: "env_store_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-central1/services/order-worker", display_name: "order-worker", repository: "github.com/acme/store-workers", identity: "order-worker@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 2 },
  { id: "svc_webhooks", organisation_id: "org_acme", application_id: "app_store", environment_id: "env_store_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-central1/services/webhook-ingress", display_name: "webhook-ingress", repository: "github.com/acme/store-api", identity: "webhook-ingress@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 2 },
  { id: "svc_billing", organisation_id: "org_acme", application_id: "app_billing", environment_id: "env_billing_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-central1/services/billing-api", display_name: "billing-api", repository: "github.com/acme/billing", identity: "billing-api@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 4 },
  { id: "svc_reconcile", organisation_id: "org_acme", application_id: "app_billing", environment_id: "env_billing_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-central1/services/reconciliation-worker", display_name: "reconciliation-worker", repository: "github.com/acme/billing", identity: "reconciliation@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 1 },
  { id: "svc_ingest", organisation_id: "org_acme", application_id: "app_data", environment_id: "env_data_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-east4/services/event-ingest", display_name: "event-ingest", repository: "github.com/acme/warehouse", identity: "event-ingest@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 2 },
  { id: "svc_exports", organisation_id: "org_acme", application_id: "app_data", environment_id: "env_data_prod", runtime_connection_id: "conn_runtime", runtime_resource: "projects/acme-prod/locations/us-east4/services/warehouse-export-worker", display_name: "warehouse-export-worker", repository: "github.com/acme/warehouse", identity: "warehouse-export@acme-prod.iam.gserviceaccount.com", created_at: earlier, updated_at: now, revision: 1 },
].map((service) => ({ ...service, telemetry_connection_ids: ["conn_telemetry"] }))

export const credentials = [
  { id: "cred_sendgrid", organisation_id: "org_acme", connection_id: "conn_sendgrid", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/sendgrid", provider: "sendgrid", kind: "api-key", display_name: "production-password-emailer", provider_id: "sg_key_4902", scopes: ["mail.send"], consumer_ids: ["svc_notifications"], active_generation_id: "gen_sendgrid_7", policy_version: "policy_prod_v3", created_at: "2026-05-18T10:00:00Z", updated_at: now, revision: 8 },
  { id: "cred_stripe", organisation_id: "org_acme", connection_id: "conn_stripe", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/stripe", provider: "stripe", kind: "api-key", display_name: "stripe-checkout-live", provider_id: "rk_live_8184", scopes: ["charges.write", "customers.read"], consumer_ids: ["svc_checkout", "svc_webhooks"], active_generation_id: "gen_stripe_5", policy_version: "policy_prod_v3", created_at: "2026-06-14T10:00:00Z", updated_at: "2026-08-15T15:20:00Z", revision: 5 },
  { id: "cred_vendor", organisation_id: "org_acme", connection_id: "conn_vendor", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/vendor", provider: "internal-vendor", kind: "api-key", display_name: "vendor-order-export", provider_id: "vendor_key_942", scopes: ["orders.read", "orders.export"], consumer_ids: ["svc_orders"], active_generation_id: "gen_vendor_3", policy_version: "policy_prod_v3", created_at: "2026-06-01T09:00:00Z", updated_at: "2026-08-10T11:00:00Z", revision: 3 },
  { id: "cred_github", organisation_id: "org_acme", connection_id: "conn_github_provider", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/github", provider: "github", kind: "fine-grained-token", display_name: "deployment-release-token", provider_id: "gh_pat_2841", scopes: ["contents:read", "deployments:write"], consumer_ids: ["svc_webhooks"], active_generation_id: "gen_github_4", policy_version: "policy_prod_v3", created_at: "2026-04-01T09:00:00Z", updated_at: now, revision: 5 },
  { id: "cred_billing", organisation_id: "org_acme", connection_id: "conn_stripe", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/billing", provider: "stripe", kind: "restricted-key", display_name: "billing-subscriptions", provider_id: "rk_live_3172", scopes: ["subscriptions.write", "invoices.read"], consumer_ids: ["svc_billing"], active_generation_id: "gen_billing_9", policy_version: "policy_finance_v2", created_at: "2026-03-12T09:00:00Z", updated_at: "2026-08-12T10:00:00Z", revision: 9 },
  { id: "cred_reconcile", organisation_id: "org_acme", connection_id: "conn_netsuite", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/reconcile", provider: "netsuite", kind: "oauth-client", display_name: "finance-reconciliation", provider_id: "oauth_client_992", scopes: ["transactions.read"], consumer_ids: ["svc_reconcile"], active_generation_id: "gen_reconcile_2", policy_version: "policy_finance_v2", created_at: "2026-07-02T09:00:00Z", updated_at: "2026-08-02T10:00:00Z", revision: 2 },
  { id: "cred_ingest", organisation_id: "org_acme", connection_id: "conn_segment", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/ingest", provider: "segment", kind: "write-key", display_name: "production-event-ingest", provider_id: "segment_18f2", scopes: ["events.write"], consumer_ids: ["svc_ingest"], active_generation_id: "gen_ingest_6", policy_version: "policy_data_v1", created_at: "2026-02-10T09:00:00Z", updated_at: "2026-08-08T10:00:00Z", revision: 6 },
  { id: "cred_exports", organisation_id: "org_acme", connection_id: "conn_snowflake", secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/exports", provider: "snowflake", kind: "key-pair", display_name: "warehouse-export-signer", provider_id: "snow_user_export", scopes: ["warehouse:use", "stage:write"], consumer_ids: ["svc_exports"], active_generation_id: "gen_exports_4", policy_version: "policy_data_v1", created_at: "2026-01-12T09:00:00Z", updated_at: "2026-08-01T10:00:00Z", revision: 4 },
]

export const bindings = credentials.flatMap((credential, credentialIndex) =>
  credential.consumer_ids.map((serviceId, consumerIndex) => {
    const service = services.find((item) => item.id === serviceId)
    return {
      id: `binding_${credentialIndex + 1}_${consumerIndex + 1}`,
      organisation_id: "org_acme",
      credential_id: credential.id,
      service_id: service.id,
      environment_id: service.environment_id,
      runtime_connection_id: service.runtime_connection_id,
      runtime_resource: service.runtime_resource,
      runtime_secret_name: credential.display_name.replaceAll("-", "_").toUpperCase(),
      secret_reference: credential.secret_reference,
      current_generation_id: credential.active_generation_id,
      target_generation_id: credential.id === "cred_sendgrid" ? "gen_sendgrid_8" : null,
      verification_id: `probe_${credential.id.replace("cred_", "")}_${consumerIndex + 1}`,
      required: true,
      revision: credentialIndex + consumerIndex + 1,
    }
  }),
)

export const generations = credentials.map((credential, index) => ({
  id: credential.active_generation_id,
  organisation_id: credential.organisation_id,
  credential_id: credential.id,
  provider_id: credential.provider_id,
  fingerprint: null,
  scopes: credential.scopes,
  state: "active",
  attempt_id: `attempt_seed_${index + 1}`,
  secret_reference: credential.secret_reference,
  predecessor_id: null,
  successor_id: null,
  created_at: credential.updated_at,
  revoked_at: null,
}))

export const runs = [
  {
    id: "run_emergency_sendgrid", organisation_id: "org_acme", credential_id: "cred_sendgrid",
    trigger: { source: "github-secret-scanning", event_id: "event_github_1842", actor_id: "actor_ingestion", reason: "Verified SendGrid key exposure in a public repository", urgency: "emergency", received_at: "2026-08-16T11:42:00Z" },
    policy_version: "policy_prod_v3", stage: "approval", status: "paused",
    lease: null, fencing_token: 4, browser_playbook_version: null, plan_id: "plan_sendgrid_42", plan_hash: hash("a"), current_generation_id: "gen_sendgrid_7", target_generation_id: "gen_sendgrid_8",
    failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: "2026-08-16T11:42:05Z", updated_at: "2026-08-16T17:43:00Z", revision: 14,
  },
  {
    id: "run_github_schedule", organisation_id: "org_acme", credential_id: "cred_github",
    trigger: { source: "scheduler", event_id: "schedule_90d_991", actor_id: "actor_scheduler", reason: "Routine 90-day credential rotation", urgency: "routine", received_at: "2026-08-16T16:20:00Z" },
    policy_version: "policy_prod_v3", stage: "deploy", status: "running",
    lease: { owner_id: "actor_coordinator", fencing_token: 2, expires_at: "2026-08-16T18:10:00Z" }, fencing_token: 2, browser_playbook_version: null, plan_id: "plan_github_91", plan_hash: hash("b"), current_generation_id: "gen_github_4", target_generation_id: "gen_github_5",
    failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: "2026-08-16T16:20:00Z", updated_at: now, revision: 8,
  },
  {
    id: "run_stripe_complete", organisation_id: "org_acme", credential_id: "cred_stripe",
    trigger: { source: "scheduler", event_id: "schedule_90d_948", actor_id: "actor_scheduler", reason: "Routine restricted key rotation", urgency: "routine", received_at: "2026-08-15T13:00:00Z" },
    policy_version: "policy_prod_v3", stage: "complete", status: "completed",
    lease: null, fencing_token: 3, browser_playbook_version: null, plan_id: "plan_stripe_44", plan_hash: hash("c"), current_generation_id: "gen_stripe_4", target_generation_id: "gen_stripe_5",
    failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: "2026-08-15T13:00:00Z", updated_at: "2026-08-15T15:20:00Z", revision: 18,
  },
  {
    id: "run_segment_complete", organisation_id: "org_acme", credential_id: "cred_ingest",
    trigger: { source: "cloud-logging", event_id: "event_segment_118", actor_id: "actor_ingestion", reason: "Verified write key exposure in an application log", urgency: "emergency", received_at: "2026-08-12T08:10:00Z" },
    policy_version: "policy_data_v1", stage: "complete", status: "completed",
    lease: null, fencing_token: 3, browser_playbook_version: null, plan_id: "plan_segment_18", plan_hash: hash("8"), current_generation_id: "gen_ingest_5", target_generation_id: "gen_ingest_6",
    failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: "2026-08-12T08:10:10Z", updated_at: "2026-08-12T09:42:00Z", revision: 17,
  },
  {
    id: "run_vendor_failed", organisation_id: "org_acme", credential_id: "cred_vendor",
    trigger: { source: "scheduler", event_id: "schedule_90d_913", actor_id: "actor_scheduler", reason: "Routine vendor key rotation", urgency: "routine", received_at: "2026-08-14T09:00:00Z" },
    policy_version: "policy_prod_v3", stage: "create", status: "failed",
    lease: null, fencing_token: 2, browser_playbook_version: "play_vendor_v1", plan_id: "plan_vendor_12", plan_hash: hash("d"), current_generation_id: "gen_vendor_3", target_generation_id: null,
    failure: { code: "provider-authentication-expired", message: "The approved browser session requires reauthentication before credential creation can continue.", retryable: true, evidence_ids: ["evidence_vendor_auth"] }, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: "2026-08-14T09:00:00Z", updated_at: "2026-08-14T09:08:00Z", revision: 6,
  },
]

export const incidents = [
  {
    id: "incident_github_1842", organisation_id: "org_acme", event_id: "event_github_1842", source: "github-secret-scanning", source_event_id: "alert-1842", severity: "critical", confidence: "verified", status: "rotation-started",
    resource: { credential_id: "cred_sendgrid", repository: "github.com/acme/store-api", project: "acme-prod", service: "notification-worker", environment: "production", provider: "sendgrid", provider_id: "sg_key_4902" },
    candidates: [{ credential_id: "cred_sendgrid", confidence: "verified", reasons: ["Exact provider key identifier", "Repository mapped to consuming service"], consumer_ids: ["svc_notifications"] }], credential_id: "cred_sendgrid", run_id: "run_emergency_sendgrid", dismissal_reason: null, created_at: "2026-08-16T11:42:05Z", updated_at: "2026-08-16T11:43:00Z", revision: 4,
  },
  {
    id: "incident_scc_9921", organisation_id: "org_acme", event_id: "event_scc_9921", source: "security-command-center", source_event_id: "finding-scc-9921", severity: "high", confidence: "high", status: "action-required",
    resource: { credential_id: "cred_vendor", repository: null, project: "acme-prod", service: "order-worker", environment: "production", provider: "internal-vendor", provider_id: "vendor_key_942" },
    candidates: [{ credential_id: "cred_vendor", confidence: "high", reasons: ["Provider identifier matched inventory", "Affected service consumes this generation"], consumer_ids: ["svc_orders"] }], credential_id: "cred_vendor", run_id: null, dismissal_reason: null, created_at: "2026-08-16T10:15:30Z", updated_at: "2026-08-16T10:16:00Z", revision: 2,
  },
  {
    id: "incident_segment_118", organisation_id: "org_acme", event_id: "event_segment_118", source: "cloud-logging", source_event_id: "log-alert-118", severity: "medium", confidence: "verified", status: "resolved",
    resource: { credential_id: "cred_ingest", repository: null, project: "acme-prod", service: "event-ingest", environment: "production", provider: "segment", provider_id: "segment_18f2" },
    candidates: [{ credential_id: "cred_ingest", confidence: "verified", reasons: ["Exact secret reference matched"], consumer_ids: ["svc_ingest"] }], credential_id: "cred_ingest", run_id: "run_segment_complete", dismissal_reason: null, created_at: "2026-08-12T08:10:00Z", updated_at: "2026-08-12T09:42:00Z", revision: 7,
  },
]

export const approvals = [
  { id: "approval_sendgrid_revoke", organisation_id: "org_acme", run_id: "run_emergency_sendgrid", action_id: "action_revoke_sg_old", action_digest: hash("e"), plan_hash: hash("a"), evidence_hash: hash("f"), generation_id: "gen_sendgrid_7", requested_by: "actor_coordinator", capability_hash: hash("1"), decision: "pending", approver_id: null, expires_at: "2026-08-17T17:43:00Z", created_at: "2026-08-16T17:43:00Z", decided_at: null, consumed_at: null, revision: 1 },
  { id: "approval_vendor_reauth", organisation_id: "org_acme", run_id: "run_vendor_failed", action_id: "action_vendor_takeover", action_digest: hash("2"), plan_hash: hash("d"), evidence_hash: hash("3"), generation_id: "gen_vendor_3", requested_by: "actor_operator", capability_hash: hash("4"), decision: "pending", approver_id: null, expires_at: "2026-08-17T09:08:00Z", created_at: "2026-08-16T17:50:00Z", decided_at: null, consumed_at: null, revision: 2 },
  { id: "approval_stripe_revoke", organisation_id: "org_acme", run_id: "run_stripe_complete", action_id: "action_revoke_stripe_old", action_digest: hash("5"), plan_hash: hash("c"), evidence_hash: hash("6"), generation_id: "gen_stripe_4", requested_by: "actor_coordinator", capability_hash: hash("7"), decision: "approved", approver_id: "actor_chigozie", expires_at: "2026-08-16T18:00:00Z", created_at: "2026-08-15T15:01:00Z", decided_at: "2026-08-15T15:04:00Z", consumed_at: "2026-08-15T15:04:10Z", revision: 3 },
]

export const policies = [
  { id: "policy_prod", organisation_id: "org_acme", name: "Production SaaS credentials", latest_version: 3, active_version_id: "policy_prod_v3", created_at: earlier, updated_at: now, revision: 4 },
  { id: "policy_finance", organisation_id: "org_acme", name: "Finance restricted access", latest_version: 2, active_version_id: "policy_finance_v2", created_at: earlier, updated_at: "2026-08-12T10:00:00Z", revision: 2 },
  { id: "policy_data", organisation_id: "org_acme", name: "Data platform service keys", latest_version: 1, active_version_id: "policy_data_v1", created_at: earlier, updated_at: "2026-08-08T10:00:00Z", revision: 1 },
].map((policy) => ({
  ...policy,
  automatic_triggers: policy.id === "policy_finance" ? ["schedule", "verified-exposure"] : ["schedule", "expiry", "drift", "verified-exposure"],
  protected_operations: ["provider.revokeCredential", "secretStore.destroyVersion"],
  rollout: [5, 25, 50, 100],
}))

export const playbooks = [
  { id: "play_vendor", organisation_id: "org_acme", name: "Vendor console credential rotation", platform: "internal-vendor", latest_version: 1, active_version_id: "play_vendor_v1", created_at: earlier, updated_at: "2026-08-10T11:00:00Z", revision: 1 },
  { id: "play_partner", organisation_id: "org_acme", name: "Partner portal credential rotation", platform: "partner-portal", latest_version: 1, active_version_id: null, created_at: earlier, updated_at: now, revision: 1 },
]

export const playbookVersions = [
  { id: "play_vendor_v1", organisation_id: "org_acme", playbook_id: "play_vendor", number: 1, state: "published", definition: { name: "Vendor console credential rotation", platform: "internal-vendor", allowed_domains: ["*.vendor.example.com"], login_url_pattern: "https://login.vendor.example.com/*", steps: [{ id: "action_create", stage: "create", tool: "browser.secure-capture", secure_field: { name: "credential" } }, { id: "action_revoke", stage: "revoke", tool: "browser.click", secure_field: null }] }, source_ids: ["source_vendor_text"], published_by: "actor_chigozie", published_at: earlier, created_at: earlier },
]

export const playbookSources = [
  { id: "source_vendor_text", organisation_id: "org_acme", playbook_id: "play_vendor", kind: "text", resource: `sha256:${hash("vendor-procedure")}`, content_type: "text/plain", size: 78, status: "ready", analysis: { source_id: "source_vendor_text", transcript: [{ start_seconds: 0, end_seconds: 0, text: "Open credential settings, create and capture the replacement, then revoke the prior key." }], screen_text: [], shots: [], redaction_count: 0, processor: "firekey-source-sanitizer", created_at: earlier }, created_by: "actor_chigozie", created_at: earlier, updated_at: earlier, revision: 0 },
]

const agentBase = { organisation_id: "org_acme", version: "2026.08.16", owner: "platform-security@acme.com", endpoint: "https://agents.firekey.acme.internal", deployment: "cloud-run://acme-agents", registry: "agent-registry://acme-prod", ingress_gateway: "agent-gateway://firekey-ingress", egress_gateway: "secure-web-proxy://firekey-egress", region: "us-central1", approved_callers: ["serviceAccount:firekey-workflow@acme-prod.iam.gserviceaccount.com"], tool_destinations: ["mcp://firekey-broker"], status: "ready", registered_at: now }
export const agents = [
  { ...agentBase, id: "agent_inventory", kind: "inventory", display_name: "Inventory & Exposure Agent", skills: ["correlate_exposure", "resolve_consumers", "detect_stale_mapping", "estimate_blast_radius"], identity: "agent-inventory@acme-prod.iam.gserviceaccount.com" },
  { ...agentBase, id: "agent_planner", kind: "planner", display_name: "Rotation Planning Agent", skills: ["plan_rotation", "select_strategy", "bind_playbook", "diagnose_failed_stage"], identity: "agent-planner@acme-prod.iam.gserviceaccount.com" },
  { ...agentBase, id: "agent_playbook", kind: "playbook", display_name: "Playbook Builder Agent", skills: ["build_playbook", "analyse_walkthrough", "validate_playbook"], identity: "agent-playbook@acme-prod.iam.gserviceaccount.com" },
  { ...agentBase, id: "agent_operator", kind: "operator", display_name: "Console Operator Agent", skills: ["execute_console_playbook", "detect_interface_drift"], identity: "agent-operator@acme-prod.iam.gserviceaccount.com" },
]

const auditKinds = [
  ["incident.correlated", "actor_inventory", "incidents/incident_github_1842", "run_emergency_sendgrid", { confidence: "verified", credential_id: "cred_sendgrid" }],
  ["run.stage.completed", "actor_coordinator", "runs/run_emergency_sendgrid/stages/verify", "run_emergency_sendgrid", { stage: "verify", generation_id: "gen_sendgrid_8" }],
  ["traffic.shifted", "actor_broker", "services/svc_notifications", "run_emergency_sendgrid", { traffic_percentage: 100, generation_id: "gen_sendgrid_8" }],
  ["approval.requested", "actor_coordinator", "approvals/approval_sendgrid_revoke", "run_emergency_sendgrid", { action: "provider.revokeCredential" }],
  ["connection.reauthentication", "actor_authbroker", "connections/conn_vendor", "run_vendor_failed", { status: "reauthentication-required" }],
  ["run.stage.started", "actor_coordinator", "runs/run_github_schedule/stages/deploy", "run_github_schedule", { stage: "deploy" }],
  ["plan.created", "actor_planner", "plans/plan_github_91", "run_github_schedule", { policy_version: "policy_prod_v3" }],
  ["verification.passed", "actor_verifier", "probes/probe_sendgrid_v1", "run_emergency_sendgrid", { status: "passed", auth_failures: 0 }],
]
export const audits = auditKinds.map(([kind, actor_id, resource, run_id, payload], index) => ({
  id: `audit_${index + 401}`,
  organisation_id: "org_acme",
  sequence: 401 + index,
  kind,
  actor_id,
  resource,
  run_id,
  payload,
  evidence_ids: [`evidence_${index + 1}`],
  previous_hash: index === 0 ? hash("0") : hash(String((index + 8) % 10)),
  event_hash: hash(String((index + 1) % 10)),
  occurred_at: `2026-08-16T17:${String(20 + index * 4).padStart(2, "0")}:00Z`,
  region: "us-central1",
}))

export const notifications = [
  { id: "notification_approval", organisation_id: "org_acme", kind: "approval-required", severity: "high", title: "Revocation approval required", body: "The replacement SendGrid generation is healthy and the old generation is ready for revocation.", link_path: "/approvals", resource_id: "approval_sendgrid_revoke", run_id: "run_emergency_sendgrid", incident_id: "incident_github_1842", approval_id: "approval_sendgrid_revoke", read_at: null, created_at: "2026-08-16T17:43:00Z", revision: 1 },
  { id: "notification_auth", organisation_id: "org_acme", kind: "connection-unhealthy", severity: "high", title: "Vendor connection requires authentication", body: "An authorised user must renew the isolated browser session before the run can resume.", link_path: "/connections", resource_id: "conn_vendor", run_id: "run_vendor_failed", incident_id: null, approval_id: null, read_at: null, created_at: "2026-08-16T17:50:00Z", revision: 1 },
]

export function createStore() {
  return structuredClone({ overview, connections, applications, environments, services, credentials, generations, bindings, runs, incidents, approvals, policies, playbooks, playbookVersions, playbookSources, agents, audits, notifications, setups: [] })
}
