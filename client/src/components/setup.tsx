import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api, type ImportCredentialInput } from "../lib/api"
import type { Application, Connection, Environment, InventoryGraph, Playbook } from "../types"
import { titleCase } from "../lib/format"
import { ControlsFields, ControlsSummary } from "./controls"
import { buildControlPreferences, controlsAreValid, defaultControls, type ControlValues } from "../lib/controls"
import { Detail, DetailList, Section } from "./detail"
import { Provider } from "./provider"
import { Button } from "./ui/button"
import { FormGrid, ResourceSelect, SelectControl, SetupPage } from "./workspace"
import { ApplicationSetup } from "../pages/applications"
import { ConnectionSetup } from "../pages/connections"

const steps = ["Credential", "Deployment", "Controls", "Review"]
const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"
const credentialKinds = [
  ["api-key", "API key"],
  ["restricted-key", "Restricted key"],
  ["oauth-client", "OAuth client"],
  ["fine-grained-token", "Fine-grained token"],
  ["write-key", "Write key"],
  ["key-pair", "Key pair"],
] as const

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80)
}

function Label({ title, children }: { title: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">{title}</span>{children}</label>
}

export function CredentialSetup({
  isOpen,
  onClose,
  graph,
  connections,
  applications,
  environments,
  playbooks,
  onCreate,
}: {
  isOpen: boolean
  onClose: () => void
  graph: InventoryGraph
  connections: Connection[]
  applications: Application[]
  environments: Environment[]
  playbooks: Playbook[]
  onCreate: (input: ImportCredentialInput) => Promise<unknown>
}) {
  const queryClient = useQueryClient()
  const initialized = useRef(false)
  const [step, setStep] = useState(0)
  const [connectionId, setConnectionId] = useState("")
  const [name, setName] = useState("")
  const [providerId, setProviderId] = useState("")
  const [kind, setKind] = useState("")
  const [scopes, setScopes] = useState("")
  const [serviceId, setServiceId] = useState("")
  const [secretStoreId, setSecretStoreId] = useState("")
  const [secretResource, setSecretResource] = useState("")
  const [secretReference, setSecretReference] = useState("")
  const [controls, setControls] = useState<ControlValues>(defaultControls)
  const [runtimeSecretNames, setRuntimeSecretNames] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [dependency, setDependency] = useState<"connection" | "secret-location" | "application" | null>(null)

  const managementConnections = useMemo(() => connections.filter((item) => item.roles.includes("provider") && item.interface === "api" && item.status === "ready" && item.capabilities.includes("provider.listCredentialMetadata")), [connections])
  const secretStores = useMemo(() => connections.filter((item) => item.roles.includes("secret-store") && item.interface === "api" && item.status === "ready"), [connections])
  const connection = connections.find((item) => item.id === connectionId)
  const secretStore = connections.find((item) => item.id === secretStoreId)
  const provider = connection?.platform ?? ""
  const selectedServices = graph.services.filter((item) => item.id === serviceId)
  const selectedService = selectedServices[0]
  const providerCredentialsQuery = useQuery({
    queryKey: ["connections", connectionId, "credential-metadata"],
    queryFn: () => api.getProviderCredentials(connectionId),
    enabled: isOpen && Boolean(connectionId),
  })
  const providerCredentials = useMemo(() => (providerCredentialsQuery.data ?? []).filter((candidate) => candidate.disabled !== true && !graph.credentials.some((credential) => credential.connection_id === connectionId && credential.provider_id === candidate.provider_id)), [connectionId, graph.credentials, providerCredentialsQuery.data])
  const secretResourcesQuery = useQuery({
    queryKey: ["connections", secretStoreId, "secret-resources"],
    queryFn: () => api.getSecretResources(secretStoreId),
    enabled: isOpen && Boolean(secretStoreId),
  })
  const secretVersionsQuery = useQuery({
    queryKey: ["connections", secretStoreId, "secret-versions", secretResource],
    queryFn: () => api.getSecretVersions(secretStoreId, secretResource),
    enabled: isOpen && Boolean(secretStoreId && secretResource),
  })

  function selectProviderCredential(nextProviderId: string) {
    setProviderId(nextProviderId)
    const selected = providerCredentials.find((candidate) => candidate.provider_id === nextProviderId)
    if (!selected) return
    setName(selected.name ?? "")
    setKind(selected.kind ?? "")
    setScopes(selected.scopes.join(", "))
  }

  useEffect(() => {
    if (!isOpen) {
      initialized.current = false
      return
    }
    if (initialized.current) return
    initialized.current = true
    setStep(0)
    setConnectionId(managementConnections[0]?.id ?? "")
    setName("")
    setProviderId("")
    setKind("")
    setScopes("")
    setServiceId(graph.services[0]?.id ?? "")
    setSecretStoreId(secretStores[0]?.id ?? "")
    setSecretResource("")
    setSecretReference("")
    setControls(defaultControls)
    setRuntimeSecretNames({})
    setSubmitting(false)
    setError("")
  }, [graph.services, isOpen, managementConnections, secretStores])

  useEffect(() => {
    if (!providerCredentials.length || providerCredentials.some((candidate) => candidate.provider_id === providerId)) return
    const first = providerCredentials[0]
    setProviderId(first.provider_id)
    setName((current) => current.trim() ? current : first.name ?? "")
    setKind(first.kind ?? "")
    setScopes(first.scopes.join(", "))
  }, [providerCredentials, providerId])

  function canContinue() {
    if (step === 0) return Boolean(connectionId && providerId && name.trim() && kind && connection?.status === "ready")
    if (step === 1) return Boolean(secretStoreId && secretReference && selectedServices.length && selectedServices.every((service) => runtimeSecretNames[service.id]?.trim()))
    if (step === 2) return controlsAreValid(controls)
    return true
  }

  function next() {
    if (step === 0 && name.trim()) {
      const fallback = slug(name).replaceAll("-", "_").toUpperCase()
      setRuntimeSecretNames((current) => Object.fromEntries(selectedServices.map((service) => [service.id, current[service.id] || fallback])))
    }
    setStep((value) => Math.min(value + 1, steps.length - 1))
  }

  async function submit() {
    if (!connection || !providerId || !selectedServices.length || !controlsAreValid(controls) || !secretStoreId || !secretReference || selectedServices.some((service) => !runtimeSecretNames[service.id]?.trim())) return
    const credentialId = identifier("cred")
    const generationId = identifier("gen")
    const controlVersionId = identifier("control_version")
    const createdAt = new Date().toISOString()
    const parsedScopes = scopes.split(",").map((item) => item.trim()).filter(Boolean)
    const input: ImportCredentialInput = {
      credential: {
        id: credentialId,
        organisation_id: "org_acme",
        connection_id: connection.id,
        secret_store_connection_id: secretStoreId,
        secret_reference: secretReference,
        provider: connection.platform,
        kind: kind.trim(),
        display_name: name.trim(),
        provider_id: providerId,
        scopes: parsedScopes,
        consumer_ids: selectedServices.map((service) => service.id),
        active_generation_id: generationId,
        control_version: controlVersionId,
        created_at: createdAt,
        updated_at: createdAt,
        revision: 0,
      },
      generation: {
        id: generationId,
        organisation_id: "org_acme",
        credential_id: credentialId,
        provider_id: providerId,
        fingerprint: null,
        scopes: parsedScopes,
        state: "active",
        attempt_id: identifier("attempt"),
        secret_reference: secretReference,
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
        secret_reference: secretReference,
        current_generation_id: generationId,
        target_generation_id: null,
        verification_id: identifier("verify"),
        required: true,
        revision: 0,
      })),
      controls: buildControlPreferences(controls),
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

  if (dependency === "connection" || dependency === "secret-location") return <ConnectionSetup
    initialRoles={dependency === "connection" ? ["provider"] : ["secret-store"]}
    playbooks={playbooks}
    onClose={() => setDependency(null)}
    onChanged={() => queryClient.invalidateQueries({ queryKey: ["connections"] })}
    onCreated={async (created) => {
      if (dependency === "connection") setConnectionId(created.id)
      else setSecretStoreId(created.id)
      setDependency(null)
    }}
  />

  if (dependency === "application") return <ApplicationSetup
    connections={connections}
    onClose={() => setDependency(null)}
    onCreated={async ({ service }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["applications"] }),
        queryClient.invalidateQueries({ queryKey: ["environments"] }),
        queryClient.invalidateQueries({ queryKey: ["graph"] }),
      ])
      setServiceId(service.id)
      setRuntimeSecretNames((current) => ({ ...current, [service.id]: slug(name).replaceAll("-", "_").toUpperCase() }))
      setDependency(null)
    }}
  />

  return (
    <SetupPage
      eyebrow="Inventory / Credentials"
      title="Add credential"
      steps={steps}
      current={step}
      onBack={() => setStep((value) => Math.max(0, value - 1))}
      onCancel={onClose}
      error={error || providerCredentialsQuery.error?.message || secretResourcesQuery.error?.message || secretVersionsQuery.error?.message}
      primary={step < steps.length - 1
        ? <Button onClick={next} disabled={!canContinue()}>Continue</Button>
        : <Button onClick={submit} disabled={submitting}>{submitting ? "Adding credential…" : "Add credential"}</Button>}
    >

      {step === 0 && <div className="grid gap-4 sm:grid-cols-2">
        <ResourceSelect label="Connection" value={connectionId} onChange={(value) => { setConnectionId(value); setProviderId(""); setName(""); setKind(""); setScopes("") }} addLabel="Add connection" onAdd={() => setDependency("connection")} className={field}><option value="">Select connection</option>{managementConnections.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect>
        <Label title="Provider credential"><SelectControl className={field} value={providerId} onChange={(event) => selectProviderCredential(event.target.value)} disabled={providerCredentialsQuery.isLoading || !providerCredentials.length}><option value="">{providerCredentialsQuery.isLoading ? "Loading credentials…" : providerCredentials.length ? "Select credential" : "No available credentials"}</option>{providerCredentials.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.name ? `${item.name} · ${item.provider_id}` : item.provider_id}</option>)}</SelectControl></Label>
        <Label title="Credential name"><input className={field} value={name} onChange={(event) => setName(event.target.value)} placeholder="production-service-key" /></Label>
        <Label title="Credential type"><SelectControl className={field} value={kind} onChange={(event) => setKind(event.target.value)}><option value="">Select type</option>{kind && !credentialKinds.some(([value]) => value === kind) && <option value={kind}>{titleCase(kind)}</option>}{credentialKinds.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></Label>
        <div className="sm:col-span-2"><Label title="Scopes"><div className={`${field} flex h-auto min-h-11 items-center py-2.5`}>{scopes || "No scopes reported"}</div></Label></div>
      </div>}

      {step === 1 && <div>
        <Section title="Secret"><FormGrid>
          <ResourceSelect label="Location" value={secretStoreId} onChange={(value) => { setSecretStoreId(value); setSecretResource(""); setSecretReference("") }} addLabel="Add connection" onAdd={() => setDependency("secret-location")} className={field}><option value="">Select location</option>{secretStores.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect>
          <Label title="Secret"><SelectControl className={field} value={secretResource} onChange={(event) => { setSecretResource(event.target.value); setSecretReference("") }} disabled={!secretStoreId || secretResourcesQuery.isLoading}><option value="">{secretResourcesQuery.isLoading ? "Loading secrets…" : "Select secret"}</option>{secretResourcesQuery.data?.map((item) => <option key={item.reference} value={item.reference}>{item.display_name}</option>)}</SelectControl></Label>
          <Label title="Version"><SelectControl className={field} value={secretReference} onChange={(event) => setSecretReference(event.target.value)} disabled={!secretResource || secretVersionsQuery.isLoading}><option value="">{secretVersionsQuery.isLoading ? "Loading versions…" : "Select enabled version"}</option>{secretVersionsQuery.data?.map((item) => <option key={item.reference} value={item.reference}>{item.reference.split("/").at(-1)}</option>)}</SelectControl></Label>
        </FormGrid></Section>
        <Section title="Consumer"><FormGrid>
          <ResourceSelect label="Application service" value={serviceId} onChange={(value) => { setServiceId(value); setRuntimeSecretNames((current) => ({ ...current, [value]: current[value] || slug(name).replaceAll("-", "_").toUpperCase() })) }} addLabel="Add application" onAdd={() => setDependency("application")} className={field}><option value="">Select service</option>{applications.map((application) => {
            const services = graph.services.filter((service) => service.application_id === application.id)
            return services.length ? <optgroup key={application.id} label={application.display_name}>{services.map((service) => <option key={service.id} value={service.id}>{service.display_name} · {environments.find((item) => item.id === service.environment_id)?.display_name}</option>)}</optgroup> : null
          })}</ResourceSelect>
          {selectedService && <Label title="Runtime secret name"><input className={field} value={runtimeSecretNames[selectedService.id] ?? ""} onChange={(event) => setRuntimeSecretNames((current) => ({ ...current, [selectedService.id]: event.target.value }))} placeholder="SERVICE_API_KEY" /></Label>}
        </FormGrid></Section>
      </div>}

      {step === 2 && <ControlsFields value={controls} onChange={setControls} />}

      {step === 3 && <div>
        <Section title="Credential"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={provider} /></Detail><Detail label="Type">{titleCase(kind)}</Detail><Detail label="Scopes">{scopes || "None"}</Detail><Detail label="Connection">{connection?.display_name}</Detail></DetailList></Section>
        <Section title="Deployment"><DetailList><Detail label="Secret">{secretStore?.display_name}</Detail><Detail label="Consumers">{selectedServices.map((service) => service.display_name).join(", ")}</Detail><Detail label="Applications">{[...new Set(selectedServices.map((service) => applications.find((item) => item.id === service.application_id)?.display_name).filter(Boolean))].join(", ")}</Detail><Detail label="Environments">{[...new Set(selectedServices.map((service) => environments.find((item) => item.id === service.environment_id)?.display_name).filter(Boolean))].join(", ")}</Detail></DetailList></Section>
        <Section title="Controls"><ControlsSummary value={controls} /></Section>
      </div>}

    </SetupPage>
  )
}
