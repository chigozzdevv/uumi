export type Identifier = string

export type ConnectionKind = "provider" | "secret-store" | "runtime" | "telemetry" | "incident" | "browser"
export type ConnectionStatus = "ready" | "reauthentication-required" | "degraded" | "disabled"

export interface HttpOperation {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  path: string
  success_statuses: number[]
  query: Record<string, string>
  body: Record<string, unknown>
  list_items: string | null
  provider_id_field: string | null
  secret_field: string | null
  name_field: string | null
}

export interface HttpProviderApi {
  base_url: string
  auth: {
    scheme: "bearer" | "header" | "basic"
    header: string
    prefix: string | null
  }
  list_credentials: HttpOperation
  create_credential: HttpOperation
  revoke_credential: HttpOperation
}

export interface Connection {
  id: Identifier
  organisation_id: Identifier
  kind: ConnectionKind
  provider: string
  display_name: string
  auth_reference: string | null
  capabilities: string[]
  allowed_resources: string[]
  http: HttpProviderApi | null
  status: ConnectionStatus
  region: string
  created_at: string
  updated_at: string
  revision: number
}

export interface Application {
  id: Identifier
  organisation_id: Identifier
  display_name: string
  repository_ids: string[]
  created_at: string
  updated_at: string
  revision: number
}

export interface Environment {
  id: Identifier
  organisation_id: Identifier
  application_id: Identifier
  display_name: string
  production: boolean
  region: string
  created_at: string
  updated_at: string
  revision: number
}

export interface ConsumerService {
  id: Identifier
  organisation_id: Identifier
  application_id: Identifier
  environment_id: Identifier
  runtime_connection_id: Identifier
  runtime_resource: string
  display_name: string
  repository: string | null
  identity: string
  created_at: string
  updated_at: string
  revision: number
}

export interface ManagedCredential {
  id: Identifier
  organisation_id: Identifier
  connection_id: Identifier
  provider: string
  kind: string
  display_name: string
  provider_id: string | null
  scopes: string[]
  consumer_ids: Identifier[]
  active_generation_id: Identifier | null
  policy_version: Identifier
  playbook_version: Identifier
  created_at: string
  updated_at: string
  revision: number
}

export type GenerationState = "creating" | "active" | "superseded" | "revoked" | "orphaned" | "unknown"

export interface CredentialGeneration {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  provider_id: string | null
  fingerprint: string | null
  scopes: string[]
  state: GenerationState
  attempt_id: Identifier
  secret_reference: string | null
  predecessor_id: Identifier | null
  successor_id: Identifier | null
  created_at: string
  revoked_at: string | null
}

export interface ConsumerBinding {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  service_id: Identifier
  environment_id: Identifier
  runtime_connection_id: Identifier
  runtime_resource: string
  secret_reference: string
  current_generation_id: Identifier
  target_generation_id: Identifier | null
  verification_id: Identifier
  required: boolean
  revision: number
}

export interface InventoryGraph {
  credentials: ManagedCredential[]
  services: ConsumerService[]
  bindings: ConsumerBinding[]
}

export interface Playbook {
  id: Identifier
  organisation_id: Identifier
  name: string
  provider: string
  latest_version: number
  active_version_id: Identifier | null
  created_at: string
  updated_at: string
  revision: number
}

export interface Policy {
  id: Identifier
  organisation_id: Identifier
  name: string
  latest_version: number
  active_version_id: Identifier | null
  created_at: string
  updated_at: string
  revision: number
}

export interface Incident {
  id: Identifier
  organisation_id: Identifier
  event_id: Identifier
  source: string
  source_event_id: string
  severity: "critical" | "high" | "medium" | "low"
  confidence: "verified" | "high" | "medium" | "low"
  status: "new" | "correlating" | "action-required" | "rotation-started" | "contained" | "resolved" | "dismissed"
  resource: {
    credential_id: Identifier | null
    repository: string | null
    project: string | null
    service: string | null
    environment: string | null
    provider: string | null
    provider_id: string | null
  }
  candidates: Array<{
    credential_id: Identifier
    confidence: "verified" | "high" | "medium" | "low"
    reasons: string[]
    consumer_ids: Identifier[]
  }>
  credential_id: Identifier | null
  run_id: Identifier | null
  dismissal_reason: string | null
  created_at: string
  updated_at: string
  revision: number
}

export type StageName =
  | "trigger"
  | "preflight"
  | "playbook"
  | "create"
  | "store"
  | "deploy"
  | "verify"
  | "rollout"
  | "observe"
  | "approval"
  | "revoke"
  | "complete"

export type RunStatus = "pending" | "running" | "paused" | "recovering" | "cleanup-required" | "failed" | "compensated" | "completed"

export interface Trigger {
  source: string
  event_id: string
  actor_id: Identifier
  reason: string
  urgency: string
  received_at: string
}

export interface RotationRun {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  trigger: Trigger
  policy_version: Identifier
  dry_run_id: Identifier | null
  dry_run_playbook_id: Identifier | null
  stage: StageName
  status: RunStatus
  lease: { owner_id: Identifier; fencing_token: number; expires_at: string } | null
  fencing_token: number
  playbook_version: Identifier | null
  plan_id: Identifier | null
  plan_hash: string | null
  current_generation_id: Identifier | null
  target_generation_id: Identifier | null
  failure: { code: string; message: string; retryable: boolean; evidence_ids: Identifier[] } | null
  recovery_id: Identifier | null
  recovery_stage: StageName | null
  recovery_mode: string | null
  recovery_failure: { code: string; message: string; retryable: boolean; evidence_ids: Identifier[] } | null
  recovery_evidence_ids: Identifier[]
  created_at: string
  updated_at: string
  revision: number
}

export interface Approval {
  id: Identifier
  organisation_id: Identifier
  run_id: Identifier
  action_id: Identifier
  action_digest: string
  plan_hash: string
  evidence_hash: string
  generation_id: Identifier
  requested_by: Identifier
  capability_hash: string
  decision: "pending" | "approved" | "rejected" | "more-evidence" | "extend-observation"
  approver_id: Identifier | null
  expires_at: string
  created_at: string
  decided_at: string | null
  consumed_at: string | null
  revision: number
}

export interface AgentRegistration {
  id: Identifier
  organisation_id: Identifier
  kind: "inventory" | "planner" | "playbook" | "operator"
  display_name: string
  version: string
  skills: string[]
  owner: string
  identity: string
  endpoint: string
  deployment: string
  registry: string
  ingress_gateway: string
  egress_gateway: string
  region: string
  approved_callers: string[]
  tool_destinations: string[]
  status: "deploying" | "ready" | "degraded" | "disabled"
  registered_at: string
}

export interface AuditEvent {
  id: Identifier
  organisation_id: Identifier
  sequence: number
  kind: string
  actor_id: Identifier
  resource: string
  run_id: Identifier | null
  payload: Record<string, string | number | boolean | null>
  evidence_ids: Identifier[]
  previous_hash: string
  event_hash: string
  occurred_at: string
  region: string
}

export interface OverviewSummary {
  credentials: number
  rotations_in_progress: number
  failed_rotations: number
  open_incidents: number
  pending_approvals: number
}
