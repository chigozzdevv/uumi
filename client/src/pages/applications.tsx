import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { AppWindow, ChevronRight, Plus, Server } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, FormGrid, SetupPage, formControl } from "../components/workspace"
import type { Application, Connection, ConsumerService, Environment } from "../types"
import { api, type CreateApplicationInput } from "../lib/api"
import { formatDate } from "../lib/format"

const setupSteps = ["Application", "Runtime", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

export function ApplicationsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [environment, setEnvironment] = useState("all")
  const [selected, setSelected] = useState<Application | null>(null)
  const [creating, setCreating] = useState(false)
  const [addingService, setAddingService] = useState(false)
  const [applications, environments, graph, connections] = useQueries({ queries: [
    { queryKey: ["applications"], queryFn: () => api.getApplications() },
    { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
  ] })

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (applications.data ?? []).filter((item) => {
      const appEnvironments = (environments.data ?? []).filter((entry) => entry.application_id === item.id)
      const matchesEnvironment = environment === "all" || (environment === "production" ? appEnvironments.some((entry) => entry.production) : appEnvironments.some((entry) => !entry.production))
      return matchesEnvironment && (!term || `${item.display_name} ${item.repository_ids.join(" ")}`.toLowerCase().includes(term))
    })
  }, [applications.data, environment, environments.data, search])

  if ([applications, environments, graph, connections].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [applications, environments, graph, connections].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selectedEnvironments = selected ? environments.data!.filter((item) => item.application_id === selected.id) : []
  const selectedServices = selected ? graph.data!.services.filter((item) => item.application_id === selected.id) : []

  if (creating) return <ApplicationSetup connections={connections.data!} onClose={() => setCreating(false)} onCreated={async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["applications"] }), queryClient.invalidateQueries({ queryKey: ["environments"] }), queryClient.invalidateQueries({ queryKey: ["graph"] })])
    setCreating(false)
  }} />

  if (selected) return <div className="page">
    <PageHeader eyebrow="Inventory / Applications" title={selected.display_name} onBack={() => setSelected(null)} actions={<Button onClick={() => setAddingService(true)}><Plus className="size-3.5" /> Add service</Button>} />
    <div className="grid gap-5 xl:grid-cols-[1.2fr_.8fr]">
      <Section title="Runtime services">
        {selectedServices.length === 0 ? <div className="rounded-xl bg-[var(--surface-soft)] p-5 text-[10px] text-[var(--ink-soft)]">No runtime services have been added yet.</div> : <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedServices.map((service) => {
          const credentials = graph.data!.bindings.filter((binding) => binding.service_id === service.id).length
          return <div key={service.id} className="flex items-center gap-3 py-4"><Marker icon={Server} /><div className="min-w-0 flex-1"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="mt-1 truncate text-[9px] text-[var(--ink-muted)]">{service.runtime_resource}</div></div><span className="text-[10px] text-[var(--ink-soft)]">{credentials} credential{credentials === 1 ? "" : "s"}</span></div>
        })}</div>}
      </Section>
      <div className="space-y-5">
        <Section title="Application"><DetailList><Detail label="Repositories">{selected.repository_ids.length || "None"}</Detail><Detail label="Environments">{selectedEnvironments.length}</Detail><Detail label="Services">{selectedServices.length}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section>
        <Section title="Environments"><div className="space-y-2">{selectedEnvironments.map((item) => <div key={item.id} className="rounded-xl border border-[var(--border-soft)] p-4"><div className="flex items-center justify-between"><div className="text-[11px] font-semibold">{item.display_name}</div><Badge variant={item.production ? "active" : "neutral"}>{item.production ? "Production" : "Non-production"}</Badge></div><div className="mt-2 text-[9px] text-[var(--ink-muted)]">{item.region}</div></div>)}</div></Section>
      </div>
    </div>
    <ServiceModal application={selected} environments={selectedEnvironments} connections={connections.data!} isOpen={addingService} onClose={() => setAddingService(false)} onCreated={async () => { await queryClient.invalidateQueries({ queryKey: ["graph"] }); setAddingService(false) }} />
  </div>

  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Applications" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add application</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search applications or repositories" resultCount={filtered.length} resultLabel="applications" onClear={() => { setSearch(""); setEnvironment("all") }} filters={[{ label: "Environment", value: environment, defaultValue: "all", onChange: (event) => setEnvironment(event.target.value), children: <><option value="all">All environments</option><option value="production">Production</option><option value="non-production">Non-production</option></> }]} />
    <Table><TableHeader><TableRow><TableHead>Application</TableHead><TableHead>Environments</TableHead><TableHead>Services</TableHead><TableHead>Credentials</TableHead><TableHead className="w-36">Action</TableHead></TableRow></TableHeader><TableBody>{filtered.map((application) => {
      const appEnvironments = environments.data!.filter((item) => item.application_id === application.id)
      const appServices = graph.data!.services.filter((item) => item.application_id === application.id)
      const credentialIds = new Set(graph.data!.bindings.filter((binding) => appServices.some((service) => service.id === binding.service_id)).map((binding) => binding.credential_id))
      return <TableRow key={application.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={() => setSelected(application)}><Marker icon={AppWindow} />{application.display_name}</button></TableCell><TableCell>{appEnvironments.length}</TableCell><TableCell>{appServices.length}</TableCell><TableCell>{credentialIds.size}</TableCell><TableCell><Button variant="ghost" size="sm" onClick={() => setSelected(application)}>View details <ChevronRight className="size-3.5" /></Button></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}

function ApplicationSetup({ onClose, connections, onCreated }: { onClose: () => void; connections: Connection[]; onCreated: () => Promise<void> }) {
  const [step, setStep] = useState(0)
  const [name, setName] = useState("")
  const [repository, setRepository] = useState("")
  const [environmentName, setEnvironmentName] = useState("Production")
  const [production, setProduction] = useState(true)
  const [region, setRegion] = useState("us-central1")
  const [serviceName, setServiceName] = useState("")
  const [runtimeConnection, setRuntimeConnection] = useState("")
  const [runtimeResource, setRuntimeResource] = useState("")
  const [identity, setIdentity] = useState("")
  const [telemetryConnection, setTelemetryConnection] = useState("")
  const mutation = useMutation({ mutationFn: (input: CreateApplicationInput) => api.createApplication(input), onSuccess: onCreated })
  const runtimes = connections.filter((item) => item.roles.includes("runtime") && item.interface === "api" && item.status === "ready")
  const telemetry = connections.filter((item) => item.roles.includes("telemetry") && item.interface === "api" && item.status === "ready")

  useEffect(() => { setRuntimeConnection(runtimes[0]?.id ?? ""); setTelemetryConnection(telemetry[0]?.id ?? "") }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const canContinue = step === 0 ? Boolean(name.trim() && environmentName.trim() && region.trim()) : step === 1 ? Boolean(serviceName.trim() && runtimeConnection && runtimeResource.trim() && identity.trim()) : true

  async function submit() {
    const applicationId = identifier("app")
    const environmentId = identifier("env")
    const timestamp = new Date().toISOString()
    await mutation.mutateAsync({
      application: { id: applicationId, organisation_id: "org_acme", display_name: name.trim(), repository_ids: repository.split(",").map((item) => item.trim()).filter(Boolean), created_at: timestamp, updated_at: timestamp, revision: 0 },
      environment: { id: environmentId, organisation_id: "org_acme", application_id: applicationId, display_name: environmentName.trim(), production, region: region.trim(), created_at: timestamp, updated_at: timestamp, revision: 0 },
      service: { id: identifier("svc"), organisation_id: "org_acme", application_id: applicationId, environment_id: environmentId, runtime_connection_id: runtimeConnection, telemetry_connection_ids: telemetryConnection ? [telemetryConnection] : [], runtime_resource: runtimeResource.trim(), display_name: serviceName.trim(), repository: repository.split(",")[0]?.trim() || null, identity: identity.trim(), created_at: timestamp, updated_at: timestamp, revision: 0 },
    })
  }

  return <SetupPage eyebrow="Inventory / Applications" title="Add application" steps={setupSteps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={mutation.error?.message} primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue}>{step === 0 ? "Continue to runtime" : "Review application"}</Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Adding…" : "Add application"}</Button>}>
    {step === 0 && <FormGrid><Field label="Application name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Store platform" /></Field><Field label="Repositories" hint="Comma-separated repository identifiers."><input className={formControl} value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="github:organisation/repository" /></Field><Field label="Environment"><input className={formControl} value={environmentName} onChange={(event) => setEnvironmentName(event.target.value)} /></Field><Field label="Region"><input className={formControl} value={region} onChange={(event) => setRegion(event.target.value)} /></Field><label className="flex items-center gap-3 rounded-xl border border-[var(--border-soft)] p-4 text-[10px] font-medium sm:col-span-2"><input type="checkbox" checked={production} onChange={(event) => setProduction(event.target.checked)} /> Production environment</label></FormGrid>}
    {step === 1 && <FormGrid><Field label="Service name"><input className={formControl} value={serviceName} onChange={(event) => setServiceName(event.target.value)} placeholder="checkout-api" /></Field><Field label="Runtime connection"><select className={formControl} value={runtimeConnection} onChange={(event) => setRuntimeConnection(event.target.value)}>{runtimes.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Field><Field label="Runtime resource" wide><input className={formControl} value={runtimeResource} onChange={(event) => setRuntimeResource(event.target.value)} placeholder="projects/project/locations/region/services/service" /></Field><Field label="Workload identity"><input className={formControl} value={identity} onChange={(event) => setIdentity(event.target.value)} placeholder="service identity" /></Field><Field label="Telemetry connection" hint="Optional health evidence used after deployment."><select className={formControl} value={telemetryConnection} onChange={(event) => setTelemetryConnection(event.target.value)}><option value="">None</option>{telemetry.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Field></FormGrid>}
    {step === 2 && <div className="grid gap-5 lg:grid-cols-2"><Section title="Application"><DetailList><Detail label="Name">{name}</Detail><Detail label="Environment">{environmentName}</Detail><Detail label="Region">{region}</Detail><Detail label="Repository">{repository || "None"}</Detail></DetailList></Section><Section title="Runtime"><DetailList><Detail label="Service">{serviceName}</Detail><Detail label="Runtime">{connections.find((item) => item.id === runtimeConnection)?.display_name}</Detail><Detail label="Telemetry">{connections.find((item) => item.id === telemetryConnection)?.display_name ?? "None"}</Detail><Detail label="Credentials">Bind after creation</Detail></DetailList></Section></div>}
  </SetupPage>
}

function ServiceModal({ application, environments, connections, isOpen, onClose, onCreated }: { application: Application; environments: Environment[]; connections: Connection[]; isOpen: boolean; onClose: () => void; onCreated: () => Promise<void> }) {
  const [name, setName] = useState("")
  const [environmentId, setEnvironmentId] = useState("")
  const [runtimeConnection, setRuntimeConnection] = useState("")
  const [runtimeResource, setRuntimeResource] = useState("")
  const [identity, setIdentity] = useState("")
  const [telemetryConnection, setTelemetryConnection] = useState("")
  const mutation = useMutation({ mutationFn: (service: ConsumerService) => api.createService(service), onSuccess: onCreated })
  const runtimes = connections.filter((item) => item.roles.includes("runtime") && item.status === "ready")
  const telemetry = connections.filter((item) => item.roles.includes("telemetry") && item.status === "ready")

  useEffect(() => { if (isOpen) { setName(""); setEnvironmentId(environments[0]?.id ?? ""); setRuntimeConnection(runtimes[0]?.id ?? ""); setRuntimeResource(""); setIdentity(""); setTelemetryConnection(""); mutation.reset() } }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    const timestamp = new Date().toISOString()
    await mutation.mutateAsync({ id: identifier("svc"), organisation_id: application.organisation_id, application_id: application.id, environment_id: environmentId, runtime_connection_id: runtimeConnection, telemetry_connection_ids: telemetryConnection ? [telemetryConnection] : [], runtime_resource: runtimeResource.trim(), display_name: name.trim(), repository: application.repository_ids[0] ?? null, identity: identity.trim(), created_at: timestamp, updated_at: timestamp, revision: 0 })
  }

  return <Modal isOpen={isOpen} onClose={onClose} title="Add runtime service" description="Connect one deployable workload to this application." actions={<Button onClick={submit} disabled={!name.trim() || !environmentId || !runtimeConnection || !runtimeResource.trim() || !identity.trim() || mutation.isPending}>{mutation.isPending ? "Adding…" : "Add service"}</Button>}>
    <div className="space-y-4"><Field label="Service name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="checkout-api" /></Field><Field label="Environment"><select className={formControl} value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)}>{environments.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Field><Field label="Runtime connection"><select className={formControl} value={runtimeConnection} onChange={(event) => setRuntimeConnection(event.target.value)}>{runtimes.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Field><Field label="Runtime resource"><input className={formControl} value={runtimeResource} onChange={(event) => setRuntimeResource(event.target.value)} /></Field><Field label="Workload identity"><input className={formControl} value={identity} onChange={(event) => setIdentity(event.target.value)} /></Field><Field label="Telemetry connection" hint="Optional health evidence for this service."><select className={formControl} value={telemetryConnection} onChange={(event) => setTelemetryConnection(event.target.value)}><option value="">None</option>{telemetry.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Field>{mutation.error && <div role="alert" className="rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{mutation.error.message}</div>}</div>
  </Modal>
}
