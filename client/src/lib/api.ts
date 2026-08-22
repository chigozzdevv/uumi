import type {
  Application,
  Approval,
  AuditEvent,
  Connection,
  ConsumerBinding,
  ConsumerService,
  CredentialGeneration,
  Environment,
  Incident,
  Identifier,
  InventoryGraph,
  ManagedCredential,
  OverviewSummary,
  Playbook,
  ProviderCredentialMetadata,
  RuntimeResourceMetadata,
  RotationRun,
} from "../types";

const ORG_ID = "org_acme"
const ROOT = `/v1/organisations/${ORG_ID}`

export interface ImportCredentialInput {
  credential: ManagedCredential
  generation: CredentialGeneration
  bindings: ConsumerBinding[]
  controls: ControlPreferences
}

export interface CreateConnectionInput {
  connection: Connection
  playbook_id?: Identifier
  playbook_version_id?: Identifier
}

export interface CreateApplicationInput {
  application: Application
  environment: Environment
  service: ConsumerService
}

export interface PlaybookDefinition {
  name: string
  platform: string
  allowed_domains: string[]
  login_url_pattern: string
  steps: Array<{
    id: Identifier
    stage: "create" | "revoke"
    effect: "none" | "create-credential" | "revoke-credential"
    tool: string
    operation: string
    objective: string
    parameters: Record<string, string | number | boolean | string[]>
    protected: boolean
    evidence_checks: string[]
    selectors: Array<{ kind: "role" | "label" | "text" | "test-id" | "css"; value: string; name: string | null; exact: boolean }>
    checkpoint: { url_pattern: string; required_text: string[]; forbidden_text: string[] }
    secure_field: { name: Identifier; selector: { kind: "role" | "label" | "text" | "test-id" | "css"; value: string; name: string | null; exact: boolean }; provider_id_selector: { kind: "role" | "label" | "text" | "test-id" | "css"; value: string; name: string | null; exact: boolean } } | null
    outputs: never[]
    timeout_seconds: number
    retry_limit: number
  }>
}

export interface CreatePlaybookInput {
  playbook_id: Identifier
  version_id: Identifier
  definition: PlaybookDefinition
  source: {
    id: Identifier
    kind: "text" | "link" | "video"
    content: string
    resource_url?: string
  }
}

export interface ControlDefinition {
  required_checks: Record<string, string[]>
  allowed_tools: string[]
  protected_tools: string[]
  allowed_recovery_modes: Array<"retry" | "rollback" | "rollforward" | "cleanup" | "escalate">
  maximum_observation_seconds: number
  preserve_old_generation: boolean
  require_functional_probe: boolean
  require_generation_telemetry: boolean
  rotate_before_expiry_seconds: number
  maximum_metadata_age_seconds: number
  require_runtime_alignment: boolean
  automatic_triggers: string[]
  emergency_triggers: string[]
  minimum_automatic_confidence: "verified" | "high" | "medium" | "low"
  probe_versions: Record<string, string[]>
  recovery: Record<string, unknown>
}

export interface ControlPreferences {
  automatic_triggers: string[]
  rotate_before_expiry_seconds: number
  maximum_observation_seconds: number
}

export interface SecretResourceMetadata {
  reference: string
  display_name: string
}

export interface SecretVersionMetadata {
  reference: string
  state: string
  created_at: string | null
}

export interface StartRotationInput {
  credential_id: Identifier
  control_version: Identifier
  reason: string
  urgency: "routine" | "urgent" | "emergency"
}

export interface BrowserSetupResponse {
  session: { id: Identifier; revision: number; expires_at: string }
  token: string
  gateway_url: string
  expires_at: string
}

export interface PlaybookVersion {
  id: Identifier
  playbook_id: Identifier
  number: number
  definition: PlaybookDefinition
  state: "draft" | "published" | "superseded" | "review-required"
  source_ids: Identifier[]
}

export interface ControlVersion {
  id: Identifier
  organisation_id: Identifier
  credential_id: Identifier
  number: number
  definition: ControlDefinition
  digest: string
  created_by: Identifier
  created_at: string
}

export interface PlaybookDetail {
  playbook: Playbook
  active_version: PlaybookVersion | null
  latest_version: PlaybookVersion | null
}

export interface CredentialControlsResponse {
  credential: ManagedCredential
  controls: ControlVersion
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(
    message: string,
    status: number,
    code: string,
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

class ApiClient {
  private async request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${import.meta.env.VITE_API_URL ?? ""}${path}`, {
      credentials: "include",
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    })
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { code?: string; message?: string } | null
      throw new ApiError(
        problem?.message ?? `FireKey API request failed (${response.status})`,
        response.status,
        problem?.code ?? "request-failed",
      )
    }
    return response.json() as Promise<T>
  }

  async getOverview(): Promise<OverviewSummary> {
    return this.request(`${ROOT}/overview`)
  }

  async getGraph(): Promise<InventoryGraph> {
    return this.request(`${ROOT}/inventory/graph`)
  }

  async getCredential(credentialId: Identifier): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}`)
  }

  async updateCredential(credentialId: Identifier, input: { expected_revision: number; display_name?: string }): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveCredential(credentialId: Identifier, expectedRevision: number): Promise<ManagedCredential> {
    return this.archive(`${ROOT}/inventory/credentials/${credentialId}`, expectedRevision)
  }

  async importCredential(input: ImportCredentialInput): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async getCredentialControls(credentialId: Identifier, versionId: Identifier): Promise<ControlVersion> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}/controls/${versionId}`)
  }

  async updateCredentialControls(
    credentialId: Identifier,
    input: { expected_revision: number; version_id: Identifier; controls: ControlPreferences },
  ): Promise<CredentialControlsResponse> {
    return this.request(`${ROOT}/inventory/credentials/${credentialId}/controls`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async createConnection(input: CreateConnectionInput): Promise<Connection> {
    const created = await this.request<Connection>(`${ROOT}/inventory/connections`, {
      method: "POST",
      body: JSON.stringify(input.connection),
    })
    if (!input.playbook_id || !input.playbook_version_id) return created
    return this.attachPlaybook(created, input.playbook_id, input.playbook_version_id)
  }

  async getConnection(connectionId: Identifier): Promise<Connection> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}`)
  }

  async resolveCredential(connectionId: Identifier, input: { secret_store_connection_id: Identifier; secret_reference: string }): Promise<ProviderCredentialMetadata> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/resolve-credential`, {
      method: "POST",
      body: JSON.stringify(input),
    })
  }

  async getRuntimeResources(connectionId: Identifier): Promise<RuntimeResourceMetadata[]> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/runtime-resources`)
  }

  async getSecretResources(connectionId: Identifier): Promise<SecretResourceMetadata[]> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/secret-resources`)
  }

  async getSecretVersions(connectionId: Identifier, secret: string): Promise<SecretVersionMetadata[]> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/secret-versions?secret=${encodeURIComponent(secret)}`)
  }

  async updateConnection(connectionId: Identifier, input: { expected_revision: number; display_name?: string; capabilities?: string[]; allowed_resources?: string[]; region?: string }): Promise<Connection> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveConnection(connectionId: Identifier, expectedRevision: number): Promise<Connection> {
    return this.archive(`${ROOT}/inventory/connections/${connectionId}`, expectedRevision)
  }

  async attachPlaybook(connection: Connection, playbookId: Identifier, versionId: Identifier): Promise<Connection> {
    return this.request(`${ROOT}/playbooks/${playbookId}/versions/${versionId}/attach`, {
      method: "POST",
      body: JSON.stringify({ connection_id: connection.id, expected_revision: connection.revision }),
    })
  }

  async beginBrowserSetup(connectionId: Identifier, secretContainer: string): Promise<BrowserSetupResponse> {
    return this.request(`${ROOT}/inventory/connections/${connectionId}/setup`, {
      method: "POST",
      body: JSON.stringify({ secret_container: secretContainer, extra_domains: [] }),
    })
  }

  async completeBrowserSetup(setupId: Identifier, expectedRevision: number, token: string): Promise<{ connection: Connection }> {
    return this.request(`${ROOT}/inventory/setups/${setupId}/complete`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, token }),
    })
  }

  async createApplication(input: CreateApplicationInput): Promise<Application> {
    const setup = await this.request<CreateApplicationInput>(`${ROOT}/inventory/application-setups`, {
      method: "POST",
      body: JSON.stringify(input),
    })
    return setup.application
  }

  async getApplication(applicationId: Identifier): Promise<Application> {
    return this.request(`${ROOT}/inventory/applications/${applicationId}`)
  }

  async updateApplication(applicationId: Identifier, input: { expected_revision: number; display_name?: string; repository_ids?: string[] }): Promise<Application> {
    return this.request(`${ROOT}/inventory/applications/${applicationId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveApplication(applicationId: Identifier, expectedRevision: number): Promise<Application> {
    return this.archive(`${ROOT}/inventory/applications/${applicationId}`, expectedRevision)
  }

  async getEnvironment(environmentId: Identifier): Promise<Environment> {
    return this.request(`${ROOT}/inventory/environments/${environmentId}`)
  }

  async updateEnvironment(environmentId: Identifier, input: { expected_revision: number; display_name?: string; production?: boolean; region?: string }): Promise<Environment> {
    return this.request(`${ROOT}/inventory/environments/${environmentId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveEnvironment(environmentId: Identifier, expectedRevision: number): Promise<Environment> {
    return this.archive(`${ROOT}/inventory/environments/${environmentId}`, expectedRevision)
  }

  async createService(service: ConsumerService): Promise<ConsumerService> {
    return this.request(`${ROOT}/inventory/services`, {
      method: "POST",
      body: JSON.stringify(service),
    })
  }

  async getService(serviceId: Identifier): Promise<ConsumerService> {
    return this.request(`${ROOT}/inventory/services/${serviceId}`)
  }

  async updateService(serviceId: Identifier, input: { expected_revision: number; display_name?: string; runtime_connection_id?: Identifier; telemetry_connection_ids?: Identifier[]; runtime_resource?: string; verification?: ConsumerService["verification"]; repository?: string | null; identity?: string | null }): Promise<ConsumerService> {
    return this.request(`${ROOT}/inventory/services/${serviceId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    })
  }

  async archiveService(serviceId: Identifier, expectedRevision: number): Promise<ConsumerService> {
    return this.archive(`${ROOT}/inventory/services/${serviceId}`, expectedRevision)
  }

  async createPlaybook(input: CreatePlaybookInput): Promise<Playbook> {
    const source = await this.request<{ id: Identifier }>(`${ROOT}/playbooks/${input.playbook_id}/walkthroughs/references`, {
      method: "POST",
      body: JSON.stringify({ source_id: input.source.id, kind: input.source.kind, content: input.source.content, resource_url: input.source.resource_url }),
    })
    const created = await this.request<{ playbook: Playbook }>(`${ROOT}/playbooks/${input.playbook_id}/build`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ version_id: input.version_id, objective: JSON.stringify(input.definition), source_ids: [source.id] }),
    })
    return created.playbook
  }

  async publishPlaybook(playbookId: Identifier, versionId: Identifier): Promise<PlaybookVersion> {
    return this.request(`${ROOT}/playbooks/${playbookId}/versions/${versionId}/publish`, {
      method: "POST",
      body: JSON.stringify({}),
    })
  }

  async getApplications(): Promise<Application[]> {
    return this.request(`${ROOT}/inventory/applications`)
  }

  async getEnvironments(): Promise<Environment[]> {
    return this.request(`${ROOT}/inventory/environments`)
  }

  async getIncidents(): Promise<Incident[]> {
    return this.request(`${ROOT}/incidents`)
  }

  async getRotations(): Promise<RotationRun[]> {
    return this.request(`${ROOT}/runs`)
  }

  async startRotation(input: StartRotationInput): Promise<RotationRun> {
    const response = await this.request<{ run: RotationRun }>(`${ROOT}/runs`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ ...input, source: "manual", event_id: `manual-${crypto.randomUUID()}`, received_at: new Date().toISOString() }),
    })
    return response.run
  }

  async getRotation(runId: string): Promise<RotationRun> {
    return this.request(`${ROOT}/runs/${runId}`)
  }

  async getApprovals(): Promise<Approval[]> {
    return this.request(`${ROOT}/approvals`)
  }

  async decideApproval(approvalId: string, expectedRevision: number, decision: "approved" | "rejected" | "more-evidence" | "extend-observation"): Promise<Approval> {
    return this.request(`${ROOT}/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, decision }),
    })
  }

  async getPlaybooks(): Promise<Playbook[]> {
    return this.request(`${ROOT}/playbooks`)
  }

  async getPlaybook(playbookId: Identifier): Promise<PlaybookDetail> {
    return this.request(`${ROOT}/playbooks/${playbookId}`)
  }

  async renamePlaybook(playbookId: Identifier, expectedRevision: number, name: string): Promise<Playbook> {
    return this.request(`${ROOT}/playbooks/${playbookId}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, name }),
    })
  }

  async archivePlaybook(playbookId: Identifier, expectedRevision: number): Promise<Playbook> {
    return this.archive(`${ROOT}/playbooks/${playbookId}`, expectedRevision)
  }

  async getConnections(): Promise<Connection[]> {
    return this.request(`${ROOT}/inventory/connections`)
  }

  async getAudits(): Promise<AuditEvent[]> {
    return this.request(`${ROOT}/audit`)
  }

  private async archive<T>(resourcePath: string, expectedRevision: number): Promise<T> {
    return this.request(`${resourcePath}/archive`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, cascade: true }),
    })
  }
}

export const api = new ApiClient()

export type {
  Application,
  Approval,
  AuditEvent,
  Connection,
  ConsumerService,
  Environment,
  Incident,
  ManagedCredential,
  OverviewSummary,
  Playbook,
  RotationRun,
}
