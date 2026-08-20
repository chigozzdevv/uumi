import { useEffect, useMemo, useState, type ReactNode } from "react"
import type { ImportCredentialInput } from "../lib/api"
import type { Application, Connection, Environment, InventoryGraph, Policy } from "../types"
import { titleCase } from "../lib/format"
import { Detail, DetailList, Section } from "./detail"
import { Provider } from "./provider"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { SetupPage } from "./workspace"

const steps = ["Management", "Storage", "Consumers", "Policy", "Review"]
const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"

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
  onCreate,
}: {
  isOpen: boolean
  onClose: () => void
  graph: InventoryGraph
  connections: Connection[]
  applications: Application[]
  environments: Environment[]
  policies: Policy[]
  onCreate: (input: ImportCredentialInput) => Promise<unknown>
}) {
  const [step, setStep] = useState(0)
  const [connectionId, setConnectionId] = useState("")
  const [name, setName] = useState("")
  const [providerId, setProviderId] = useState("")
  const [kind, setKind] = useState("api-key")
  const [scopes, setScopes] = useState("")
  const [serviceIds, setServiceIds] = useState<string[]>([])
  const [secretStoreId, setSecretStoreId] = useState("")
  const [secretReference, setSecretReference] = useState("")
  const [policyVersion, setPolicyVersion] = useState("")
  const [runtimeSecretNames, setRuntimeSecretNames] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const managementConnections = useMemo(() => connections.filter((item) => item.roles.includes("provider")), [connections])
  const secretStores = useMemo(() => connections.filter((item) => item.roles.includes("secret-store") && item.interface === "api"), [connections])
  const connection = connections.find((item) => item.id === connectionId)
  const provider = connection?.platform ?? ""
  const selectedServices = graph.services.filter((item) => serviceIds.includes(item.id))
  const selectedPolicy = policies.find((item) => item.active_version_id === policyVersion)

  useEffect(() => {
    if (!isOpen) return
    setStep(0)
    setConnectionId(managementConnections.find((item) => item.status === "ready")?.id ?? managementConnections[0]?.id ?? "")
    setName("")
    setProviderId("")
    setKind("api-key")
    setScopes("")
    setServiceIds(graph.services[0] ? [graph.services[0].id] : [])
    setSecretStoreId(secretStores[0]?.id ?? "")
    setSecretReference("")
    setPolicyVersion(policies.find((item) => item.active_version_id)?.active_version_id ?? "")
    setRuntimeSecretNames({})
    setSubmitting(false)
    setError("")
  }, [graph.services, isOpen, managementConnections, policies, secretStores])

  function canContinue() {
    if (step === 0) return Boolean(connectionId && name.trim() && kind.trim() && connection?.status === "ready")
    if (step === 1) return Boolean(secretStoreId && secretReference.trim())
    if (step === 2) return Boolean(selectedServices.length && selectedServices.every((service) => runtimeSecretNames[service.id]?.trim()))
    if (step === 3) return Boolean(policyVersion)
    return true
  }

  function next() {
    if (step === 0 && !secretReference && name.trim()) setSecretReference(`projects/acme-prod/secrets/${slug(name)}`)
    if (step === 1 && name.trim()) {
      const fallback = slug(name).replaceAll("-", "_").toUpperCase()
      setRuntimeSecretNames((current) => Object.fromEntries(selectedServices.map((service) => [service.id, current[service.id] || fallback])))
    }
    setStep((value) => Math.min(value + 1, steps.length - 1))
  }

  async function submit() {
    if (!connection || !selectedServices.length || !policyVersion || !secretStoreId || !secretReference.trim() || selectedServices.some((service) => !runtimeSecretNames[service.id]?.trim())) return
    const credentialId = identifier("cred")
    const generationId = identifier("gen")
    const createdAt = new Date().toISOString()
    const parsedScopes = scopes.split(",").map((item) => item.trim()).filter(Boolean)
    const input: ImportCredentialInput = {
      credential: {
        id: credentialId,
        organisation_id: "org_acme",
        connection_id: connection.id,
        secret_store_connection_id: secretStoreId,
        secret_reference: secretReference.trim(),
        provider: connection.platform,
        kind: kind.trim(),
        display_name: name.trim(),
        provider_id: providerId.trim() || null,
        scopes: parsedScopes,
        consumer_ids: selectedServices.map((service) => service.id),
        active_generation_id: generationId,
        policy_version: policyVersion,
        created_at: createdAt,
        updated_at: createdAt,
        revision: 0,
      },
      generation: {
        id: generationId,
        organisation_id: "org_acme",
        credential_id: credentialId,
        provider_id: providerId.trim() || null,
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
      bindings: selectedServices.map((service) => ({
        id: identifier("binding"),
        organisation_id: "org_acme",
        credential_id: credentialId,
        service_id: service.id,
        environment_id: service.environment_id,
        runtime_connection_id: service.runtime_connection_id,
        runtime_resource: service.runtime_resource,
        runtime_secret_name: runtimeSecretNames[service.id].trim(),
        secret_reference: secretReference.trim(),
        current_generation_id: generationId,
        target_generation_id: null,
        verification_id: identifier("verify"),
        required: true,
        revision: 0,
      })),
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
    <SetupPage
      eyebrow="Inventory / Credentials"
      title="Add credential"
      description="Map one workload credential to its management access, storage location, consumers, and control policy. FireKey never asks for the secret value."
      steps={steps}
      current={step}
      onBack={() => setStep((value) => Math.max(0, value - 1))}
      onCancel={onClose}
      error={error}
      primary={step < steps.length - 1
        ? <Button onClick={next} disabled={!canContinue()}>{["Continue to storage", "Continue to consumers", "Continue to policy", "Review credential"][step]}</Button>
        : <Button onClick={submit} disabled={submitting}>{submitting ? "Adding credential…" : "Add credential"}</Button>}
    >

      {step === 0 && <div className="grid gap-4 sm:grid-cols-2">
        <Label title="Management connection"><select className={field} value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>{managementConnections.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label>
        <Label title="Credential name"><input className={field} value={name} onChange={(event) => setName(event.target.value)} placeholder="production-service-key" /></Label>
        <Label title="Provider credential ID (optional)"><input className={field} value={providerId} onChange={(event) => setProviderId(event.target.value)} placeholder="Provider metadata ID" /></Label>
        <Label title="Credential type"><input className={field} value={kind} onChange={(event) => setKind(event.target.value)} placeholder="api-key" /></Label>
        <Label title="Scopes"><input className={field} value={scopes} onChange={(event) => setScopes(event.target.value)} placeholder="read, write" /></Label>
        {connection?.interface === "browser" && <div className="sm:col-span-2 rounded-xl bg-[var(--accent-soft)] p-4 text-[10px] text-[var(--accent)]">Uses the published Playbook attached to this browser connection.</div>}
        {connection?.status !== "ready" && <div className="sm:col-span-2 rounded-xl border border-[#ead6b8] bg-[var(--amber-soft)] p-4 text-[10px] text-[var(--amber)]">Finish this connection before importing a credential.</div>}
      </div>}

      {step === 1 && <div className="grid gap-4 sm:grid-cols-2">
        <Label title="Secret store"><select className={field} value={secretStoreId} onChange={(event) => setSecretStoreId(event.target.value)}>{secretStores.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label>
        <div className="sm:col-span-2"><Label title="Secret reference"><input className={field} value={secretReference} onChange={(event) => setSecretReference(event.target.value)} placeholder="projects/acme-prod/secrets/service-key" /></Label></div>
      </div>}

      {step === 2 && <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2 space-y-3">{graph.services.map((service) => {
          const selected = serviceIds.includes(service.id)
          const application = applications.find((item) => item.id === service.application_id)
          const environment = environments.find((item) => item.id === service.environment_id)
          return <div key={service.id} className="rounded-xl border border-[var(--border)] bg-white/70 p-4"><label className="flex items-start gap-3"><input className="mt-0.5" type="checkbox" checked={selected} onChange={(event) => { setServiceIds((current) => event.target.checked ? [...current, service.id] : current.filter((id) => id !== service.id)); if (event.target.checked) setRuntimeSecretNames((current) => ({ ...current, [service.id]: current[service.id] || slug(name).replaceAll("-", "_").toUpperCase() })) }} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-semibold">{service.display_name}</span><span className="mt-1 block text-[9px] text-[var(--ink-muted)]">{application?.display_name} · {environment?.display_name}</span></span></label>{selected && <div className="mt-3"><Label title="Runtime secret name"><input className={field} value={runtimeSecretNames[service.id] ?? ""} onChange={(event) => setRuntimeSecretNames((current) => ({ ...current, [service.id]: event.target.value }))} placeholder="SERVICE_API_KEY" /></Label></div>}</div>
        })}</div>
      </div>}

      {step === 3 && <div className="grid gap-4 sm:grid-cols-2">
        <Label title="Rotation policy"><select className={field} value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)}>{policies.filter((item) => item.active_version_id).map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select></Label>
        <div className="sm:col-span-2 rounded-xl bg-white/70 p-4 text-[10px] text-[var(--ink-soft)]">The policy controls triggers, approvals, rollout, verification, and recovery.</div>
      </div>}

      {step === 4 && <div>
        <Section title="Credential"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={provider} /></Detail><Detail label="Type">{titleCase(kind)}</Detail><Detail label="Interface">{titleCase(connection?.interface ?? "")}</Detail></DetailList></Section>
        <Section title="Mapping"><DetailList><Detail label="Consumers">{selectedServices.map((service) => service.display_name).join(", ")}</Detail><Detail label="Applications">{[...new Set(selectedServices.map((service) => applications.find((item) => item.id === service.application_id)?.display_name).filter(Boolean))].join(", ")}</Detail><Detail label="Environments">{[...new Set(selectedServices.map((service) => environments.find((item) => item.id === service.environment_id)?.display_name).filter(Boolean))].join(", ")}</Detail><Detail label="Secret store">{connections.find((item) => item.id === secretStoreId)?.display_name}</Detail></DetailList></Section>
        <Section title="Controls"><DetailList><Detail label="Policy">{selectedPolicy?.name}</Detail><Detail label="Browser Playbook">{connection?.interface === "browser" ? connection.playbook_version_id : "Not required"}</Detail><Detail label="Connection"><Badge variant={connection?.status === "ready" ? "healthy" : "warning"}>{titleCase(connection?.status ?? "unknown")}</Badge></Detail></DetailList></Section>
      </div>}

    </SetupPage>
  )
}
