import { useEffect, useMemo, useState, type ReactNode } from "react"
import { ArrowLeft, ArrowRight, FileInput, Settings2 } from "lucide-react"
import type { ImportCredentialInput } from "../lib/api"
import type { Application, Connection, Environment, InventoryGraph, Playbook, Policy } from "../types"
import { titleCase } from "../lib/format"
import { Detail, DetailList, Section } from "./detail"
import { Journey } from "./journey"
import { Provider } from "./provider"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Modal } from "./ui/modal"

const steps = ["Method", "Credential", "Mapping", "Controls", "Review"]
const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"

type Method = "import" | "manual" | ""

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80)
}

function Label({ title, children }: { title: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[10px] font-medium text-[var(--ink-soft)]">{title}</span>{children}</label>
}

export function CredentialSetup({
  isOpen,
  onClose,
  graph,
  connections,
  applications,
  environments,
  policies,
  playbooks,
  onCreate,
}: {
  isOpen: boolean
  onClose: () => void
  graph: InventoryGraph
  connections: Connection[]
  applications: Application[]
  environments: Environment[]
  policies: Policy[]
  playbooks: Playbook[]
  onCreate: (input: ImportCredentialInput) => Promise<unknown>
}) {
  const [step, setStep] = useState(0)
  const [method, setMethod] = useState<Method>("")
  const [connectionId, setConnectionId] = useState("")
  const [name, setName] = useState("")
  const [providerId, setProviderId] = useState("")
  const [kind, setKind] = useState("api-key")
  const [scopes, setScopes] = useState("")
  const [serviceId, setServiceId] = useState("")
  const [secretStoreId, setSecretStoreId] = useState("")
  const [secretReference, setSecretReference] = useState("")
  const [policyVersion, setPolicyVersion] = useState("")
  const [playbookVersion, setPlaybookVersion] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const managementConnections = useMemo(() => connections.filter((item) => method === "import" ? item.kind === "provider" : method === "manual" ? item.kind === "browser" : false), [connections, method])
  const secretStores = useMemo(() => connections.filter((item) => item.kind === "secret-store"), [connections])
  const connection = connections.find((item) => item.id === connectionId)
  const provider = connection?.provider ?? ""
  const service = graph.services.find((item) => item.id === serviceId)
  const environment = environments.find((item) => item.id === service?.environment_id)
  const application = applications.find((item) => item.id === service?.application_id)
  const selectedPolicy = policies.find((item) => item.active_version_id === policyVersion)
  const availablePlaybooks = playbooks.filter((item) => item.provider === provider && item.active_version_id)
  const selectedPlaybook = playbooks.find((item) => item.active_version_id === playbookVersion)

  useEffect(() => {
    if (!isOpen) return
    setStep(0)
    setMethod("")
    setConnectionId("")
    setName("")
    setProviderId("")
    setKind("api-key")
    setScopes("")
    setServiceId(graph.services[0]?.id ?? "")
    setSecretStoreId(secretStores[0]?.id ?? "")
    setSecretReference("")
    setPolicyVersion(policies.find((item) => item.active_version_id)?.active_version_id ?? "")
    setPlaybookVersion("")
    setSubmitting(false)
    setError("")
  }, [graph.services, isOpen, policies, secretStores])

  useEffect(() => {
    if (!provider) return
    const active = playbooks.find((item) => item.provider === provider && item.active_version_id)
    setPlaybookVersion(active?.active_version_id ?? "")
  }, [playbooks, provider])

  function choose(nextMethod: Exclude<Method, "">) {
    const choices = connections.filter((item) => nextMethod === "import" ? item.kind === "provider" : item.kind === "browser")
    setMethod(nextMethod)
    setConnectionId(choices[0]?.id ?? "")
    setStep(1)
  }

  function canContinue() {
    if (step === 0) return false
    if (step === 1) return Boolean(connectionId && name.trim() && kind.trim() && (method === "manual" || providerId.trim()))
    if (step === 2) return Boolean(serviceId && secretStoreId && secretReference.trim())
    if (step === 3) return Boolean(policyVersion && playbookVersion)
    return true
  }

  function next() {
    if (step === 1 && !secretReference && name.trim()) setSecretReference(`projects/acme-prod/secrets/${slug(name)}`)
    setStep((value) => Math.min(value + 1, steps.length - 1))
  }

  async function submit() {
    if (!connection || !service || !policyVersion || !playbookVersion || !secretReference.trim()) return
    const credentialId = identifier("cred")
    const generationId = identifier("gen")
    const createdAt = new Date().toISOString()
    const parsedScopes = scopes.split(",").map((item) => item.trim()).filter(Boolean)
    const input: ImportCredentialInput = {
      credential: {
        id: credentialId,
        organisation_id: "org_acme",
        connection_id: connection.id,
        provider: connection.provider,
        kind: kind.trim(),
        display_name: name.trim(),
        provider_id: method === "import" ? providerId.trim() : null,
        scopes: parsedScopes,
        consumer_ids: [service.id],
        active_generation_id: generationId,
        policy_version: policyVersion,
        playbook_version: playbookVersion,
        created_at: createdAt,
        updated_at: createdAt,
        revision: 0,
      },
      generation: {
        id: generationId,
        organisation_id: "org_acme",
        credential_id: credentialId,
        provider_id: method === "import" ? providerId.trim() : null,
        fingerprint: null,
        scopes: parsedScopes,
        state: "active",
        attempt_id: identifier("attempt"),
        secret_reference: secretReference.trim(),
        predecessor_id: null,
        successor_id: null,
        created_at: createdAt,
        revoked_at: null,
      },
      bindings: [{
        id: identifier("binding"),
        organisation_id: "org_acme",
        credential_id: credentialId,
        service_id: service.id,
        environment_id: service.environment_id,
        runtime_connection_id: service.runtime_connection_id,
        runtime_resource: service.runtime_resource,
        secret_reference: secretReference.trim(),
        current_generation_id: generationId,
        target_generation_id: null,
        verification_id: identifier("verify"),
        required: true,
        revision: 0,
      }],
    }

    setSubmitting(true)
    setError("")
    try {
      await onCreate(input)
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Credential could not be added")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Add credential"
      size="wide"
      footerStart={step > 0 && <Button variant="ghost" onClick={() => setStep((value) => Math.max(0, value - 1))} disabled={submitting}><ArrowLeft className="size-3.5" /> Back</Button>}
      actions={step > 0 && (step < steps.length - 1 ? <Button onClick={next} disabled={!canContinue()}>Continue <ArrowRight className="size-3.5" /></Button> : <Button onClick={submit} disabled={submitting}>{submitting ? "Adding…" : "Add credential"}</Button>)}
    >
      <Journey steps={steps} current={step} />

      {step === 0 && <div className="grid gap-3 sm:grid-cols-2">
        <button className="focus-ring flex min-h-24 items-center gap-4 rounded-2xl border border-[var(--border)] bg-white p-5 text-left hover:border-[#bdb9cf]" onClick={() => choose("import")}><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><FileInput className="size-4" /></span><span className="flex-1 text-[12px] font-semibold">Import from provider</span><ArrowRight className="size-4 text-[var(--ink-muted)]" /></button>
        <button className="focus-ring flex min-h-24 items-center gap-4 rounded-2xl border border-[var(--border)] bg-white p-5 text-left hover:border-[#bdb9cf]" onClick={() => choose("manual")}><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#ececea] text-[var(--ink-soft)]"><Settings2 className="size-4" /></span><span className="flex-1 text-[12px] font-semibold">Configure manually</span><ArrowRight className="size-4 text-[var(--ink-muted)]" /></button>
      </div>}

      {step === 1 && <div className="grid gap-4 sm:grid-cols-2">
        <Label title="Management connection"><select className={field} value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>{managementConnections.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label>
        <Label title="Credential name"><input className={field} value={name} onChange={(event) => setName(event.target.value)} placeholder="production-service-key" /></Label>
        {method === "import" && <Label title="Provider credential ID"><input className={field} value={providerId} onChange={(event) => setProviderId(event.target.value)} placeholder="Provider metadata ID" /></Label>}
        <Label title="Credential type"><input className={field} value={kind} onChange={(event) => setKind(event.target.value)} placeholder="api-key" /></Label>
        <Label title="Scopes"><input className={field} value={scopes} onChange={(event) => setScopes(event.target.value)} placeholder="read, write" /></Label>
      </div>}

      {step === 2 && <div className="grid gap-4 sm:grid-cols-2">
        <Label title="Consumer service"><select className={field} value={serviceId} onChange={(event) => setServiceId(event.target.value)}>{graph.services.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label>
        <Label title="Secret store"><select className={field} value={secretStoreId} onChange={(event) => setSecretStoreId(event.target.value)}>{secretStores.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label>
        <div className="sm:col-span-2"><Label title="Secret reference"><input className={field} value={secretReference} onChange={(event) => setSecretReference(event.target.value)} placeholder="projects/acme-prod/secrets/service-key" /></Label></div>
        <div className="sm:col-span-2 grid grid-cols-2 gap-3 rounded-xl bg-white/70 p-4 text-[10px]"><div><span className="text-[var(--ink-muted)]">Application</span><div className="mt-1 font-semibold">{application?.display_name ?? "—"}</div></div><div><span className="text-[var(--ink-muted)]">Environment</span><div className="mt-1 font-semibold">{environment?.display_name ?? "—"}</div></div></div>
      </div>}

      {step === 3 && <div className="grid gap-4 sm:grid-cols-2">
        <Label title="Rotation policy"><select className={field} value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)}>{policies.filter((item) => item.active_version_id).map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select></Label>
        <Label title="Playbook"><select className={field} value={playbookVersion} onChange={(event) => setPlaybookVersion(event.target.value)}><option value="">Select playbook</option>{availablePlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select></Label>
        {connection?.status !== "ready" && <div className="sm:col-span-2 rounded-xl border border-[#ead6b8] bg-[var(--amber-soft)] p-4 text-[10px] text-[var(--amber)]">This management connection requires reauthentication.</div>}
      </div>}

      {step === 4 && <div>
        <Section title="Credential"><DetailList><Detail label="Name">{name}</Detail><Detail label="Provider"><Provider value={provider} /></Detail><Detail label="Type">{titleCase(kind)}</Detail><Detail label="Method">{method === "import" ? "Provider API" : "Computer Use"}</Detail></DetailList></Section>
        <Section title="Mapping"><DetailList><Detail label="Consumer">{service?.display_name}</Detail><Detail label="Application">{application?.display_name}</Detail><Detail label="Environment">{environment?.display_name}</Detail><Detail label="Secret store">{connections.find((item) => item.id === secretStoreId)?.display_name}</Detail></DetailList></Section>
        <Section title="Controls"><DetailList><Detail label="Policy">{selectedPolicy?.name}</Detail><Detail label="Playbook">{selectedPlaybook?.name}</Detail><Detail label="Connection"><Badge variant={connection?.status === "ready" ? "healthy" : "warning"}>{titleCase(connection?.status ?? "unknown")}</Badge></Detail></DetailList></Section>
      </div>}

      {error && <div role="alert" className="mt-5 rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{error}</div>}

    </Modal>
  )
}
