import type {
  AgentRegistration,
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
  Policy,
  RotationRun,
} from "../types";

const ORG_ID = "org_acme"
const ROOT = `/v1/organisations/${ORG_ID}`

export interface ImportCredentialInput {
  credential: ManagedCredential
  generation: CredentialGeneration
  bindings: ConsumerBinding[]
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

export interface PolicyDefinition {
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

export interface CreatePolicyInput {
  policy_id: Identifier
  version_id: Identifier
  name: string
  definition: PolicyDefinition
  activate: boolean
}

export interface StartRotationInput {
  credential_id: Identifier
  policy_version: Identifier
  reason: string
  urgency: "routine" | "urgent" | "emergency"
}

export interface BrowserSetupResponse {
  session: { id: Identifier; revision: number; expires_at: string }
  token: string
  gateway_url: string
  expires_at: string
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
      const problem = (await response.json().catch(() => null)) as { message?: string } | null
      throw new Error(problem?.message ?? `FireKey API request failed (${response.status})`)
    }
    return response.json() as Promise<T>
  }

  async getOverview(): Promise<OverviewSummary> {
    return this.request(`${ROOT}/overview`)
  }

  async getGraph(): Promise<InventoryGraph> {
    return this.request(`${ROOT}/inventory/graph`)
  }

  async importCredential(input: ImportCredentialInput): Promise<ManagedCredential> {
    return this.request(`${ROOT}/inventory/credentials`, {
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

  async createService(service: ConsumerService): Promise<ConsumerService> {
    return this.request(`${ROOT}/inventory/services`, {
      method: "POST",
      body: JSON.stringify(service),
    })
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
    await this.request(`${ROOT}/playbooks/${input.playbook_id}/versions/${input.version_id}/publish`, {
      method: "POST",
      body: JSON.stringify({}),
    })
    return { ...created.playbook, active_version_id: input.version_id }
  }

  async createPolicy(input: CreatePolicyInput): Promise<Policy> {
    const policy = await this.request<Policy>(`${ROOT}/policies`, {
      method: "POST",
      body: JSON.stringify({ id: input.policy_id, name: input.name }),
    })
    await this.request(`${ROOT}/policies/${input.policy_id}/versions`, {
      method: "POST",
      body: JSON.stringify({ id: input.version_id, definition: input.definition }),
    })
    if (input.activate) {
      await this.request(`${ROOT}/policies/${input.policy_id}/versions/${input.version_id}/activate`, { method: "POST", body: JSON.stringify({}) })
    }
    return { ...policy, latest_version: 1, active_version_id: input.activate ? input.version_id : null }
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

  async getPolicies(): Promise<Policy[]> {
    return this.request(`${ROOT}/policies`)
  }

  async getAgents(): Promise<AgentRegistration[]> {
    return this.request(`${ROOT}/agents`)
  }

  async getConnections(): Promise<Connection[]> {
    return this.request(`${ROOT}/inventory/connections`)
  }

  async getAudits(): Promise<AuditEvent[]> {
    return this.request(`${ROOT}/audit`)
  }
}

export const api = new ApiClient()

export type {
  AgentRegistration,
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
  Policy,
  RotationRun,
}
