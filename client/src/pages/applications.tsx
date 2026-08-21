import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { AppWindow, ChevronRight, Plus, Server } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, FormGrid, ResourceSelect, SelectControl, SetupPage, formControl } from "../components/workspace"
import type { Application, Connection, ConsumerService, Environment } from "../types"
import { api, type CreateApplicationInput } from "../lib/api"
import { formatDate } from "../lib/format"
import { ConnectionSetup } from "./connections"

const setupSteps = ["Application", "Runtime", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

export function ApplicationsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [environment, setEnvironment] = useState("all")
  const [selected, setSelected] = useState<Application | null>(null)
  const [tab, setTab] = useState<"overview" | "services" | "environments">("overview")
  const [creating, setCreating] = useState(false)
  const [addingService, setAddingService] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState("")
  const [editingEnvironment, setEditingEnvironment] = useState<Environment | null>(null)
  const [environmentName, setEnvironmentName] = useState("")
  const [editingService, setEditingService] = useState<ConsumerService | null>(null)
  const [serviceName, setServiceName] = useState("")
  const [serviceRuntime, setServiceRuntime] = useState("")
  const [serviceResource, setServiceResource] = useState("")
  const [serviceTelemetry, setServiceTelemetry] = useState("")
  const [serviceVerification, setServiceVerification] = useState("")
  const [connectionDependency, setConnectionDependency] = useState<{ role: "runtime" | "telemetry"; target: "new-service" | "edit-service" } | null>(null)
  const [addedServiceConnection, setAddedServiceConnection] = useState<Connection | null>(null)
  const [applications, environments, graph, connections] = useQueries({ queries: [
    { queryKey: ["applications"], queryFn: () => api.getApplications() },
    { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
  ] })
  const applicationDetail = useQuery({ queryKey: ["applications", selected?.id], queryFn: () => api.getApplication(selected!.id), enabled: Boolean(selected) })
  const currentSelected = applicationDetail.data ?? selected
  const updateApplication = useMutation({
    mutationFn: () => api.updateApplication(currentSelected!.id, { expected_revision: currentSelected!.revision, display_name: editName.trim() }),
    onSuccess: async (application) => {
      queryClient.setQueryData(["applications", application.id], application)
      setSelected(application)
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ["applications"] })
    },
  })
  const archiveApplication = useMutation({
    mutationFn: () => api.archiveApplication(currentSelected!.id, currentSelected!.revision),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["applications", currentSelected!.id] })
      setEditing(false)
      setSelected(null)
      await queryClient.invalidateQueries({ queryKey: ["applications"] })
    },
  })
  const updateEnvironment = useMutation({
    mutationFn: () => api.updateEnvironment(editingEnvironment!.id, { expected_revision: editingEnvironment!.revision, display_name: environmentName, production: environmentName === "Production", region: editingEnvironment!.region }),
    onSuccess: async () => { setEditingEnvironment(null); await queryClient.invalidateQueries({ queryKey: ["environments"] }) },
  })
  const archiveEnvironment = useMutation({
    mutationFn: () => api.archiveEnvironment(editingEnvironment!.id, editingEnvironment!.revision),
    onSuccess: async () => { setEditingEnvironment(null); await queryClient.invalidateQueries({ queryKey: ["environments"] }) },
  })
  const updateService = useMutation({
    mutationFn: () => api.updateService(editingService!.id, { expected_revision: editingService!.revision, display_name: serviceName.trim(), runtime_connection_id: serviceRuntime, telemetry_connection_ids: serviceTelemetry ? [serviceTelemetry] : [], runtime_resource: serviceResource.trim(), verification: verification(serviceVerification) }),
    onSuccess: async () => { setEditingService(null); await queryClient.invalidateQueries({ queryKey: ["graph"] }) },
  })
  const archiveService = useMutation({
    mutationFn: () => api.archiveService(editingService!.id, editingService!.revision),
    onSuccess: async () => { setEditingService(null); await queryClient.invalidateQueries({ queryKey: ["graph"] }) },
  })

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

  const selectedEnvironments = currentSelected ? environments.data!.filter((item) => item.application_id === currentSelected.id) : []
  const selectedServices = currentSelected ? graph.data!.services.filter((item) => item.application_id === currentSelected.id) : []
  const selectedCredentialCount = new Set(graph.data!.bindings.filter((binding) => selectedServices.some((service) => service.id === binding.service_id)).map((binding) => binding.credential_id)).size

  if (creating) return <ApplicationSetup connections={connections.data!} onClose={() => setCreating(false)} onCreated={async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["applications"] }), queryClient.invalidateQueries({ queryKey: ["environments"] }), queryClient.invalidateQueries({ queryKey: ["graph"] })])
    setCreating(false)
  }} />

  if (currentSelected) return <>
  <div className={connectionDependency ? "hidden" : "page"}>
    <PageHeader eyebrow="Inventory / Applications" title={currentSelected.display_name} onBack={() => setSelected(null)} actions={<><Button onClick={() => setAddingService(true)}><Plus className="size-3.5" /> Add service</Button><Button variant="secondary" onClick={() => { setEditName(currentSelected.display_name); updateApplication.reset(); archiveApplication.reset(); setEditing(true) }}>Edit</Button></>} />
    <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "services", label: "Services" }, { id: "environments", label: "Environments" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Environments">{selectedEnvironments.length}</Detail><Detail label="Services">{selectedServices.length}</Detail><Detail label="Credentials">{selectedCredentialCount}</Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "services" && <div>
        {selectedServices.length === 0 ? <div className="rounded-xl bg-[var(--surface-soft)] p-5 text-[10px] text-[var(--ink-soft)]">No runtime services have been added yet.</div> : <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedServices.map((service) => {
          const credentials = graph.data!.bindings.filter((binding) => binding.service_id === service.id).length
          return <div key={service.id} className="flex items-center gap-3 py-4"><Marker icon={Server} /><div className="min-w-0 flex-1"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="mt-1 truncate text-[9px] text-[var(--ink-muted)]">{selectedEnvironments.find((item) => item.id === service.environment_id)?.display_name} · {service.runtime_resource}</div></div><span className="text-[10px] text-[var(--ink-soft)]">{credentials} credential{credentials === 1 ? "" : "s"}</span><Button variant="ghost" size="sm" onClick={() => { setEditingService(service); setServiceName(service.display_name); setServiceRuntime(service.runtime_connection_id); setServiceResource(service.runtime_resource); setServiceTelemetry(service.telemetry_connection_ids[0] ?? ""); setServiceVerification(service.verification?.target ?? ""); updateService.reset(); archiveService.reset() }}>Edit</Button></div>
        })}</div>}
      </div>
      }
      {tab === "environments" && <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedEnvironments.map((item) => <div key={item.id} className="flex items-center gap-3 py-4"><div className="min-w-0 flex-1 text-[11px] font-semibold">{item.display_name}</div><Button variant="ghost" size="sm" onClick={() => { setEditingEnvironment(item); setEnvironmentName(item.production ? "Production" : "Staging"); updateEnvironment.reset(); archiveEnvironment.reset() }}>Edit</Button></div>)}</div>}
    </DetailCard>
    <ServiceModal application={currentSelected} environments={selectedEnvironments} connections={connections.data!} isOpen={addingService} onClose={() => setAddingService(false)} onCreated={async () => { await queryClient.invalidateQueries({ queryKey: ["graph"] }); setAddingService(false) }} onAddConnection={(role) => setConnectionDependency({ role, target: "new-service" })} addedConnection={addedServiceConnection} />
    <ManageResourceModal isOpen={editing} onClose={() => setEditing(false)} title="Edit application" resourceLabel="application" onSave={() => updateApplication.mutate()} onDelete={() => archiveApplication.mutate()} dependencies={[
      { label: "Environments", items: selectedEnvironments.map((item) => item.display_name) },
      { label: "Services", items: selectedServices.map((item) => item.display_name) },
      { label: "Credentials", items: graph.data!.credentials.filter((credential) => graph.data!.bindings.some((binding) => binding.credential_id === credential.id && selectedServices.some((service) => service.id === binding.service_id))).map((credential) => credential.display_name) },
    ]} saveDisabled={!editName.trim() || editName.trim() === currentSelected.display_name} saving={updateApplication.isPending} deleting={archiveApplication.isPending} error={(updateApplication.error ?? archiveApplication.error)?.message}>
      <Field label="Application name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field>
    </ManageResourceModal>
    <ManageResourceModal isOpen={Boolean(editingEnvironment)} onClose={() => setEditingEnvironment(null)} title="Edit environment" resourceLabel="environment" onSave={() => updateEnvironment.mutate()} onDelete={() => archiveEnvironment.mutate()} dependencies={[
      { label: "Services", items: selectedServices.filter((service) => service.environment_id === editingEnvironment?.id).map((service) => service.display_name) },
    ]} saveDisabled={!environmentName || environmentName === editingEnvironment?.display_name} saving={updateEnvironment.isPending} deleting={archiveEnvironment.isPending} error={(updateEnvironment.error ?? archiveEnvironment.error)?.message}>
      <Field label="Environment"><SelectControl value={environmentName} onChange={(event) => setEnvironmentName(event.target.value)}><option value="Production">Production</option><option value="Staging">Staging</option></SelectControl></Field>
    </ManageResourceModal>
    <ManageResourceModal isOpen={Boolean(editingService)} onClose={() => setEditingService(null)} title="Edit runtime service" resourceLabel="service" onSave={() => updateService.mutate()} onDelete={() => archiveService.mutate()} dependencies={[
      { label: "Credentials", items: graph.data!.credentials.filter((credential) => graph.data!.bindings.some((binding) => binding.credential_id === credential.id && binding.service_id === editingService?.id)).map((credential) => credential.display_name) },
    ]} saveDisabled={!serviceName.trim() || !serviceRuntime || !serviceResource.trim() || !serviceVerification.trim() || (serviceName.trim() === editingService?.display_name && serviceRuntime === editingService?.runtime_connection_id && serviceResource.trim() === editingService?.runtime_resource && serviceTelemetry === (editingService?.telemetry_connection_ids[0] ?? "") && serviceVerification.trim() === editingService?.verification?.target)} saving={updateService.isPending} deleting={archiveService.isPending} error={(updateService.error ?? archiveService.error)?.message}>
      <div className="space-y-4"><Field label="Service name"><input className={formControl} value={serviceName} onChange={(event) => setServiceName(event.target.value)} /></Field><ResourceSelect label="Runtime connection" value={serviceRuntime} onChange={setServiceRuntime} addLabel="Add connection" onAdd={() => setConnectionDependency({ role: "runtime", target: "edit-service" })}>{connections.data!.filter((item) => item.roles.includes("runtime") && item.interface === "api").map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect><Field label="Runtime resource"><input className={formControl} value={serviceResource} onChange={(event) => setServiceResource(event.target.value)} /></Field><Field label="Verification URL"><input className={formControl} type="url" value={serviceVerification} onChange={(event) => setServiceVerification(event.target.value)} /></Field><ResourceSelect label="Telemetry connection" value={serviceTelemetry} onChange={setServiceTelemetry} addLabel="Add connection" onAdd={() => setConnectionDependency({ role: "telemetry", target: "edit-service" })}><option value="">None</option>{connections.data!.filter((item) => item.roles.includes("telemetry") && item.interface === "api").map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect></div>
    </ManageResourceModal>
  </div>
  {connectionDependency && <ConnectionSetup
    initialRoles={[connectionDependency.role]}
    playbooks={[]}
    onClose={() => setConnectionDependency(null)}
    onChanged={() => queryClient.invalidateQueries({ queryKey: ["connections"] })}
    onCreated={async (connection) => {
      if (connectionDependency.target === "edit-service") {
        if (connectionDependency.role === "runtime") setServiceRuntime(connection.id)
        else setServiceTelemetry(connection.id)
      } else setAddedServiceConnection(connection)
      setConnectionDependency(null)
    }}
  />}
  </>

  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Applications" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add application</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search applications" onClear={() => { setSearch(""); setEnvironment("all") }} filters={[{ label: "Environment", value: environment, defaultValue: "all", onChange: (event) => setEnvironment(event.target.value), children: <><option value="all">All environments</option><option value="production">Production</option><option value="staging">Staging</option></> }]} />
    <Table><TableHeader><TableRow><TableHead>Application</TableHead><TableHead>Environments</TableHead><TableHead>Services</TableHead><TableHead>Credentials</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody>{filtered.map((application) => {
      const appEnvironments = environments.data!.filter((item) => item.application_id === application.id)
      const appServices = graph.data!.services.filter((item) => item.application_id === application.id)
      const credentialIds = new Set(graph.data!.bindings.filter((binding) => appServices.some((service) => service.id === binding.service_id)).map((binding) => binding.credential_id))
      const open = () => { setSelected(application); setTab("overview") }
      return <TableRow key={application.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={open}><Marker icon={AppWindow} />{application.display_name}</button></TableCell><TableCell>{appEnvironments.length}</TableCell><TableCell>{appServices.length}</TableCell><TableCell>{credentialIds.size}</TableCell><TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={open}>View details <ChevronRight className="size-3.5" /></Button></div></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}

export interface CreatedApplicationSetup {
  application: Application
  service: ConsumerService
}

export function ApplicationSetup({ onClose, connections, onCreated }: { onClose: () => void; connections: Connection[]; onCreated: (created: CreatedApplicationSetup) => Promise<void> }) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [name, setName] = useState("")
  const [environmentName, setEnvironmentName] = useState("Production")
  const [serviceName, setServiceName] = useState("")
  const [runtimeConnection, setRuntimeConnection] = useState("")
  const [runtimeResource, setRuntimeResource] = useState("")
  const [verificationUrl, setVerificationUrl] = useState("")
  const [telemetryConnection, setTelemetryConnection] = useState("")
  const [connectionDependency, setConnectionDependency] = useState<"runtime" | "telemetry" | null>(null)
  const mutation = useMutation({ mutationFn: (input: CreateApplicationInput) => api.createApplication(input) })
  const runtimes = connections.filter((item) => item.roles.includes("runtime") && item.interface === "api" && item.status === "ready")
  const telemetry = connections.filter((item) => item.roles.includes("telemetry") && item.interface === "api" && item.status === "ready")

  useEffect(() => { setRuntimeConnection(runtimes[0]?.id ?? ""); setTelemetryConnection(telemetry[0]?.id ?? "") }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const canContinue = step === 0 ? Boolean(name.trim() && environmentName) : step === 1 ? Boolean(serviceName.trim() && runtimeConnection && runtimeResource.trim() && verificationUrl.trim()) : true

  async function submit() {
    const applicationId = identifier("app")
    const environmentId = identifier("env")
    const timestamp = new Date().toISOString()
    const runtimeRegion = connections.find((item) => item.id === runtimeConnection)?.region ?? "us-central1"
    const input: CreateApplicationInput = {
      application: { id: applicationId, organisation_id: "org_acme", display_name: name.trim(), repository_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 },
      environment: { id: environmentId, organisation_id: "org_acme", application_id: applicationId, display_name: environmentName, production: environmentName === "Production", region: runtimeRegion, created_at: timestamp, updated_at: timestamp, revision: 0 },
      service: { id: identifier("svc"), organisation_id: "org_acme", application_id: applicationId, environment_id: environmentId, runtime_connection_id: runtimeConnection, telemetry_connection_ids: telemetryConnection ? [telemetryConnection] : [], runtime_resource: runtimeResource.trim(), display_name: serviceName.trim(), verification: verification(verificationUrl), repository: null, identity: null, created_at: timestamp, updated_at: timestamp, revision: 0 },
    }
    const application = await mutation.mutateAsync(input)
    await onCreated({ application, service: input.service })
  }

  if (connectionDependency) return <ConnectionSetup
    initialRoles={[connectionDependency]}
    playbooks={[]}
    onClose={() => setConnectionDependency(null)}
    onChanged={() => queryClient.invalidateQueries({ queryKey: ["connections"] })}
    onCreated={async (connection) => {
      if (connectionDependency === "runtime") setRuntimeConnection(connection.id)
      else setTelemetryConnection(connection.id)
      setConnectionDependency(null)
    }}
  />

  return <SetupPage eyebrow="Inventory / Applications" title="Add application" steps={setupSteps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={mutation.error?.message} primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue}>Continue</Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Adding…" : "Add application"}</Button>}>
    {step === 0 && <FormGrid><Field label="Application name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Store platform" /></Field><Field label="Environment"><SelectControl value={environmentName} onChange={(event) => setEnvironmentName(event.target.value)}><option value="Production">Production</option><option value="Staging">Staging</option></SelectControl></Field></FormGrid>}
    {step === 1 && <FormGrid><Field label="Service name"><input className={formControl} value={serviceName} onChange={(event) => setServiceName(event.target.value)} placeholder="checkout-api" /></Field><ResourceSelect label="Runtime connection" value={runtimeConnection} onChange={setRuntimeConnection} addLabel="Add connection" onAdd={() => setConnectionDependency("runtime")}>{runtimes.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect><Field label="Runtime resource" wide><input className={formControl} value={runtimeResource} onChange={(event) => setRuntimeResource(event.target.value)} placeholder="projects/project/locations/region/services/service" /></Field><Field label="Verification URL"><input className={formControl} type="url" value={verificationUrl} onChange={(event) => setVerificationUrl(event.target.value)} placeholder="https://service.example/verify" /></Field><ResourceSelect label="Telemetry connection" value={telemetryConnection} onChange={setTelemetryConnection} addLabel="Add connection" onAdd={() => setConnectionDependency("telemetry")}><option value="">None</option>{telemetry.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect></FormGrid>}
    {step === 2 && <div className="grid gap-5 lg:grid-cols-2"><Section title="Application"><DetailList><Detail label="Name">{name}</Detail><Detail label="Environment">{environmentName}</Detail></DetailList></Section><Section title="Runtime"><DetailList><Detail label="Service">{serviceName}</Detail><Detail label="Runtime">{connections.find((item) => item.id === runtimeConnection)?.display_name}</Detail><Detail label="Verification">{verificationUrl}</Detail><Detail label="Telemetry">{connections.find((item) => item.id === telemetryConnection)?.display_name ?? "None"}</Detail></DetailList></Section></div>}
  </SetupPage>
}

function ServiceModal({ application, environments, connections, isOpen, onClose, onCreated, onAddConnection, addedConnection }: { application: Application; environments: Environment[]; connections: Connection[]; isOpen: boolean; onClose: () => void; onCreated: () => Promise<void>; onAddConnection: (role: "runtime" | "telemetry") => void; addedConnection: Connection | null }) {
  const [name, setName] = useState("")
  const [environmentId, setEnvironmentId] = useState("")
  const [runtimeConnection, setRuntimeConnection] = useState("")
  const [runtimeResource, setRuntimeResource] = useState("")
  const [verificationUrl, setVerificationUrl] = useState("")
  const [telemetryConnection, setTelemetryConnection] = useState("")
  const mutation = useMutation({ mutationFn: (service: ConsumerService) => api.createService(service), onSuccess: onCreated })
  const runtimes = connections.filter((item) => item.roles.includes("runtime") && item.status === "ready")
  const telemetry = connections.filter((item) => item.roles.includes("telemetry") && item.status === "ready")

  useEffect(() => { if (isOpen) { setName(""); setEnvironmentId(environments[0]?.id ?? ""); setRuntimeConnection(runtimes[0]?.id ?? ""); setRuntimeResource(""); setVerificationUrl(""); setTelemetryConnection(""); mutation.reset() } }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!addedConnection) return
    if (addedConnection.roles.includes("runtime")) setRuntimeConnection(addedConnection.id)
    if (addedConnection.roles.includes("telemetry")) setTelemetryConnection(addedConnection.id)
  }, [addedConnection])

  async function submit() {
    const timestamp = new Date().toISOString()
    await mutation.mutateAsync({ id: identifier("svc"), organisation_id: application.organisation_id, application_id: application.id, environment_id: environmentId, runtime_connection_id: runtimeConnection, telemetry_connection_ids: telemetryConnection ? [telemetryConnection] : [], runtime_resource: runtimeResource.trim(), display_name: name.trim(), verification: verification(verificationUrl), repository: application.repository_ids[0] ?? null, identity: null, created_at: timestamp, updated_at: timestamp, revision: 0 })
  }

  return <Modal isOpen={isOpen} onClose={onClose} title="Add runtime service" actions={<Button onClick={submit} disabled={!name.trim() || !environmentId || !runtimeConnection || !runtimeResource.trim() || !verificationUrl.trim() || mutation.isPending}>{mutation.isPending ? "Adding…" : "Add service"}</Button>}>
    <div className="space-y-4"><Field label="Service name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="checkout-api" /></Field><Field label="Environment"><SelectControl value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)}>{environments.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</SelectControl></Field><ResourceSelect label="Runtime connection" value={runtimeConnection} onChange={setRuntimeConnection} addLabel="Add connection" onAdd={() => onAddConnection("runtime")}>{runtimes.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect><Field label="Runtime resource"><input className={formControl} value={runtimeResource} onChange={(event) => setRuntimeResource(event.target.value)} /></Field><Field label="Verification URL"><input className={formControl} type="url" value={verificationUrl} onChange={(event) => setVerificationUrl(event.target.value)} /></Field><ResourceSelect label="Telemetry connection" value={telemetryConnection} onChange={setTelemetryConnection} addLabel="Add connection" onAdd={() => onAddConnection("telemetry")}><option value="">None</option>{telemetry.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect>{mutation.error && <div role="alert" className="rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{mutation.error.message}</div>}</div>
  </Modal>
}

function verification(target: string): NonNullable<ConsumerService["verification"]> {
  return {
    kind: "http",
    target: target.trim(),
    method: "POST",
    expected_status: [200],
    required_fields: { success: true },
    confirmation: null,
    timeout_seconds: 30,
  }
}
