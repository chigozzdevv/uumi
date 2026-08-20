import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { AppWindow, ArrowLeft, ArrowRight, Plus, Server } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Journey } from "../components/journey"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Application } from "../types"
import { api, type CreateApplicationInput } from "../lib/api"
import { formatDate } from "../lib/format"

const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"
const setupSteps = ["Application", "Runtime", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function Label({ title, children }: { title: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[10px] font-medium text-[var(--ink-soft)]">{title}</span>{children}</label>
}

export function ApplicationsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [environment, setEnvironment] = useState("all")
  const [selected, setSelected] = useState<Application | null>(null)
  const [creating, setCreating] = useState(false)
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

  return (
    <div className="page">
      <PageHeader section="Inventory · Applications" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add application</Button>} />
      <Toolbar value={search} onChange={setSearch} placeholder="Search applications or repositories" filters={[{ label: "Environment", value: environment, onChange: (event) => setEnvironment(event.target.value), children: <><option value="all">All environments</option><option value="production">Production</option><option value="non-production">Non-production</option></> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Application</TableHead><TableHead>Environments</TableHead><TableHead>Services</TableHead><TableHead>Credentials</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader><TableBody>{filtered.map((application) => {
        const appEnvironments = environments.data!.filter((item) => item.application_id === application.id)
        const appServices = graph.data!.services.filter((item) => item.application_id === application.id)
        const credentialIds = new Set(graph.data!.bindings.filter((binding) => appServices.some((service) => service.id === binding.service_id)).map((binding) => binding.credential_id))
        return <TableRow key={application.id} className="cursor-pointer" onClick={() => setSelected(application)}><TableCell><div className="flex items-center gap-3"><Marker icon={AppWindow} /><span className="font-medium">{application.display_name}</span></div></TableCell><TableCell>{appEnvironments.length}</TableCell><TableCell>{appServices.length}</TableCell><TableCell>{credentialIds.size}</TableCell><TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(application.updated_at)}</TableCell></TableRow>
      })}</TableBody></Table></div>
      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Application"}>
        {selected && <><Section title="Application"><DetailList><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Environments">{selectedEnvironments.length}</Detail><Detail label="Services">{selectedServices.length}</Detail></DetailList></Section><Section title="Environments"><div className="space-y-2">{selectedEnvironments.map((item) => <div key={item.id} className="rounded-xl border border-[var(--border-soft)] bg-white/60 p-4"><div className="flex items-center justify-between"><div className="text-[11px] font-semibold">{item.display_name}</div><Badge variant={item.production ? "active" : "neutral"}>{item.production ? "Production" : "Non-production"}</Badge></div><div className="mt-2 text-[10px] text-[var(--ink-soft)]">{item.region} · {selectedServices.filter((service) => service.environment_id === item.id).length} services</div></div>)}</div></Section><Section title="Runtime services"><div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedServices.map((service) => { const credentials = graph.data!.bindings.filter((binding) => binding.service_id === service.id).length; return <div key={service.id} className="flex items-center gap-3 py-3.5"><span className="grid size-7 place-items-center rounded-lg bg-[#ececea] text-[var(--ink-soft)]"><Server className="size-3.5" /></span><div className="min-w-0 flex-1"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{service.telemetry_connection_ids.length} telemetry connection{service.telemetry_connection_ids.length === 1 ? "" : "s"}</div></div><span className="text-[10px] text-[var(--ink-soft)]">{credentials} credential{credentials === 1 ? "" : "s"}</span></div> })}</div></Section></>}
      </Modal>
      <ApplicationSetup isOpen={creating} onClose={() => setCreating(false)} connections={connections.data!} onCreated={async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["applications"] }), queryClient.invalidateQueries({ queryKey: ["environments"] }), queryClient.invalidateQueries({ queryKey: ["graph"] })]) }} />
    </div>
  )
}

function ApplicationSetup({ isOpen, onClose, connections, onCreated }: { isOpen: boolean; onClose: () => void; connections: Awaited<ReturnType<typeof api.getConnections>>; onCreated: () => Promise<void> }) {
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

  useEffect(() => {
    if (!isOpen) return
    setStep(0); setName(""); setRepository(""); setEnvironmentName("Production"); setProduction(true); setRegion("us-central1"); setServiceName(""); setRuntimeConnection(runtimes[0]?.id ?? ""); setRuntimeResource(""); setIdentity(""); setTelemetryConnection(telemetry[0]?.id ?? ""); mutation.reset()
  }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  function canContinue() {
    if (step === 0) return Boolean(name.trim() && environmentName.trim() && region.trim())
    if (step === 1) return Boolean(serviceName.trim() && runtimeConnection && runtimeResource.trim() && identity.trim())
    return true
  }

  async function submit() {
    const applicationId = identifier("app")
    const environmentId = identifier("env")
    const timestamp = new Date().toISOString()
    await mutation.mutateAsync({
      application: { id: applicationId, organisation_id: "org_acme", display_name: name.trim(), repository_ids: repository.split(",").map((item) => item.trim()).filter(Boolean), created_at: timestamp, updated_at: timestamp, revision: 0 },
      environment: { id: environmentId, organisation_id: "org_acme", application_id: applicationId, display_name: environmentName.trim(), production, region: region.trim(), created_at: timestamp, updated_at: timestamp, revision: 0 },
      service: { id: identifier("svc"), organisation_id: "org_acme", application_id: applicationId, environment_id: environmentId, runtime_connection_id: runtimeConnection, telemetry_connection_ids: telemetryConnection ? [telemetryConnection] : [], runtime_resource: runtimeResource.trim(), display_name: serviceName.trim(), repository: repository.split(",")[0]?.trim() || null, identity: identity.trim(), created_at: timestamp, updated_at: timestamp, revision: 0 },
    })
    onClose()
  }

  return <Modal isOpen={isOpen} onClose={onClose} title="Add application" size="wide" footerStart={step > 0 ? <Button variant="ghost" onClick={() => setStep((value) => value - 1)}><ArrowLeft className="size-3.5" /> Back</Button> : undefined} actions={step < setupSteps.length - 1 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>Continue <ArrowRight className="size-3.5" /></Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Adding…" : "Add application"}</Button>}>
    <Journey steps={setupSteps} current={step} />
    {step === 0 && <div className="grid gap-4 sm:grid-cols-2"><Label title="Application name"><input className={field} value={name} onChange={(event) => setName(event.target.value)} placeholder="Store platform" /></Label><Label title="Repositories"><input className={field} value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="github:organisation/repository" /></Label><Label title="Environment"><input className={field} value={environmentName} onChange={(event) => setEnvironmentName(event.target.value)} /></Label><Label title="Region"><input className={field} value={region} onChange={(event) => setRegion(event.target.value)} /></Label><label className="sm:col-span-2 flex items-center gap-3 rounded-xl bg-white/70 p-4 text-[10px] font-medium"><input type="checkbox" checked={production} onChange={(event) => setProduction(event.target.checked)} /> Production environment</label></div>}
    {step === 1 && <div className="grid gap-4 sm:grid-cols-2"><Label title="Service name"><input className={field} value={serviceName} onChange={(event) => setServiceName(event.target.value)} placeholder="checkout-api" /></Label><Label title="Runtime connection"><select className={field} value={runtimeConnection} onChange={(event) => setRuntimeConnection(event.target.value)}>{runtimes.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label><div className="sm:col-span-2"><Label title="Runtime resource"><input className={field} value={runtimeResource} onChange={(event) => setRuntimeResource(event.target.value)} placeholder="projects/acme-prod/locations/us-central1/services/checkout-api" /></Label></div><Label title="Workload identity"><input className={field} value={identity} onChange={(event) => setIdentity(event.target.value)} placeholder="service@project.iam.gserviceaccount.com" /></Label><Label title="Telemetry connection (optional)"><select className={field} value={telemetryConnection} onChange={(event) => setTelemetryConnection(event.target.value)}><option value="">None</option>{telemetry.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Label></div>}
    {step === 2 && <><Section title="Application"><DetailList><Detail label="Name">{name}</Detail><Detail label="Environment">{environmentName}</Detail><Detail label="Region">{region}</Detail><Detail label="Repository">{repository || "None"}</Detail></DetailList></Section><Section title="Runtime"><DetailList><Detail label="Service">{serviceName}</Detail><Detail label="Runtime">{connections.find((item) => item.id === runtimeConnection)?.display_name}</Detail><Detail label="Telemetry">{connections.find((item) => item.id === telemetryConnection)?.display_name ?? "None"}</Detail><Detail label="Credentials">Bind after the application is created</Detail></DetailList></Section></>}
    {mutation.error && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{mutation.error.message}</div>}
  </Modal>
}
