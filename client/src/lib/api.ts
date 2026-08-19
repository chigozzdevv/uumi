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
