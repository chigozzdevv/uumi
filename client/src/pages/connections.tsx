import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, ExternalLink, PlugZap, Plus, Upload } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { IntegrationGrid, IntegrationMark, type IntegrationKind } from "../components/integration"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { ConnectPage, Field, FormGrid, ResourceSelect, SelectControl, SetupPage, SuccessPage, formControl } from "../components/workspace"
import type { Connection, ConnectionRole, HttpProviderApi, Playbook } from "../types"
import { activeOrganisationId, api, type CreateConnectionInput, type GitHubDiscoveryResponse, type GitHubOnboardingResponse, type GoogleCloudOnboardingResponse, type GoogleCloudProject } from "../lib/api"
import { parseProviderAdapter } from "../lib/adapter"
import { connectionCallbackIntegration } from "../lib/callback"
import { connectionAction, connectionStatus, formatDate, titleCase } from "../lib/format"
import { PlaybookSetup } from "./playbooks"

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

function roleLabel(connection: Connection) {
  return connection.roles.map(titleCase).join(", ")
}

export function ConnectionsPage({ initialConnectionId = "" }: { initialConnectionId?: string }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [role, setRole] = useState("all")
  const [selected, setSelected] = useState<Connection | null>(null)
  const [tab, setTab] = useState<"overview" | "access">("overview")
  const [selectedPlaybookVersion, setSelectedPlaybookVersion] = useState("")
  const [creating, setCreating] = useState(() => connectionCallbackIntegration() !== null)
  const [editing, setEditing] = useState(false)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const [initialSelectionHandled, setInitialSelectionHandled] = useState(false)
  const [editName, setEditName] = useState("")
  const [connections, playbooks, graph] = useQueries({ queries: [
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  const connectionDetail = useQuery({ queryKey: ["connections", selected?.id], queryFn: () => api.getConnection(selected!.id), enabled: Boolean(selected) })
  const currentSelected = connectionDetail.data ?? selected
  const browserPlaybooks = (playbooks.data ?? []).filter((item) => item.platform === currentSelected?.platform && item.active_version_id)
  const attach = useMutation({
    mutationFn: ({ connection, playbook }: { connection: Connection; playbook: Playbook }) => api.attachPlaybook(connection, playbook.id, playbook.active_version_id!),
    onSuccess: async (connection) => {
      queryClient.setQueryData(["connections", connection.id], connection)
      setSelected(connection)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const open = useMutation({ mutationFn: (id: string) => api.beginBrowserSetup(id) })
  const updateConnection = useMutation({
    mutationFn: () => api.updateConnection(currentSelected!.id, { expected_revision: currentSelected!.revision, display_name: editName.trim() }),
    onSuccess: async (connection) => {
      queryClient.setQueryData(["connections", connection.id], connection)
      setSelected(connection)
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const archiveConnection = useMutation({
    mutationFn: () => api.archiveConnection(currentSelected!.id, currentSelected!.revision),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["connections", currentSelected!.id] })
      setEditing(false)
      setSelected(null)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })

  useEffect(() => {
    if (initialSelectionHandled || !initialConnectionId || !connections.data) return
    const connection = connections.data.find((item) => item.id === initialConnectionId)
    if (connection) {
      setSelected(connection)
      setTab("overview")
    }
    setInitialSelectionHandled(true)
  }, [connections.data, initialConnectionId, initialSelectionHandled])

  useEffect(() => {
    if (currentSelected?.interface !== "browser") return
    setSelectedPlaybookVersion(browserPlaybooks[0]?.active_version_id ?? "")
  }, [currentSelected?.id, currentSelected?.interface]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const refresh = () => { void queryClient.invalidateQueries({ queryKey: ["connections"] }) }
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [queryClient])

  async function openBrowser(connection: Connection) {
    const setup = await open.mutateAsync(connection.id)
    const fragment = new URLSearchParams({ organisation_id: connection.organisation_id, setup_id: setup.session.id, token: setup.token })
    window.open(`${setup.gateway_url}#${fragment}`, "_blank", "noopener,noreferrer")
  }
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (connections.data ?? []).filter((item) => (role === "all" || item.roles.includes(role as ConnectionRole)) && (!term || `${item.display_name} ${item.platform} ${item.roles.join(" ")} ${item.interface}`.toLowerCase().includes(term)))
  }, [connections.data, role, search])
  if ([connections, playbooks, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [connections, playbooks, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  if (creating) return <ConnectionSetup onClose={() => setCreating(false)} playbooks={playbooks.data!} connections={connections.data!} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} />

  if (creatingPlaybook && currentSelected) return <PlaybookSetup initialPlatform={currentSelected.platform} onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => {
    await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    setSelectedPlaybookVersion(playbook.active_version_id ?? "")
    setCreatingPlaybook(false)
  }} />

  if (currentSelected) return <div className="page">
    <PageHeader eyebrow="Inventory / Connections" title={currentSelected.display_name} onBack={() => setSelected(null)} actions={<>{currentSelected.interface === "browser" && currentSelected.playbook_version_id && <Button onClick={() => openBrowser(currentSelected)} disabled={open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.display_name); updateConnection.reset(); archiveConnection.reset(); setEditing(true) }}>Edit</Button></>} />
    <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "access", label: currentSelected.interface === "browser" ? "Browser access" : "Access" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Roles">{roleLabel(currentSelected)}</Detail><Detail label="Interface">{titleCase(currentSelected.interface)}</Detail><Detail label="Authorization">{titleCase(currentSelected.authorization)}</Detail><Detail label="Status"><Badge variant={connectionStatus(currentSelected.status).variant}>{connectionStatus(currentSelected.status).label}</Badge></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "access" && (currentSelected.interface === "browser" ? <div className="max-w-[620px] space-y-5"><DetailList><Detail label="Playbook version">{currentSelected.playbook_version_id ?? "Not attached"}</Detail><Detail label="Session expires">{currentSelected.authorization_expires_at ? formatDate(currentSelected.authorization_expires_at, true) : "Authentication required"}</Detail></DetailList>{!currentSelected.playbook_version_id && <div className="space-y-3"><ResourceSelect label="Playbook" value={selectedPlaybookVersion} onChange={setSelectedPlaybookVersion} addLabel="Add playbook" onAdd={() => setCreatingPlaybook(true)}><option value="">Select playbook</option>{browserPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</ResourceSelect><Button onClick={() => { const playbook = browserPlaybooks.find((item) => item.active_version_id === selectedPlaybookVersion); if (playbook) attach.mutate({ connection: currentSelected, playbook }) }} disabled={!selectedPlaybookVersion || attach.isPending}>Attach playbook</Button></div>}</div> : <DetailList><Detail label="Allowed resources">{currentSelected.allowed_resources.join(", ")}</Detail><Detail label="Capabilities">{currentSelected.capabilities.length}</Detail><Detail label="Last validated">{currentSelected.last_validated_at ? formatDate(currentSelected.last_validated_at, true) : "Never"}</Detail></DetailList>)}
    </DetailCard>
    {(attach.error || open.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(attach.error ?? open.error)?.message}</div>}
    <ManageResourceModal isOpen={editing} onClose={() => setEditing(false)} title="Edit connection" resourceLabel="connection" onSave={() => updateConnection.mutate()} onDelete={() => archiveConnection.mutate()} dependencies={[
      { label: "Credentials", items: graph.data!.credentials.filter((credential) => currentSelected.id === credential.connection_id || currentSelected.id === credential.secret_store_connection_id).map((credential) => credential.display_name) },
      { label: "Services", items: graph.data!.services.filter((service) => currentSelected.id === service.runtime_connection_id || service.telemetry_connection_ids.includes(currentSelected.id)).map((service) => service.display_name) },
    ]} saveDisabled={!editName.trim() || editName.trim() === currentSelected.display_name} saving={updateConnection.isPending} deleting={archiveConnection.isPending} error={(updateConnection.error ?? archiveConnection.error)?.message}>
      <Field label="Connection name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field>
    </ManageResourceModal>
  </div>

  return <div className="page">
    <PageHeader eyebrow="Inventory" title="Connections" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add connection</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search connections or platforms" onClear={() => { setSearch(""); setRole("all") }} filters={[{ label: "Role", value: role, defaultValue: "all", onChange: (event) => setRole(event.target.value), children: <><option value="all">All roles</option>{[...new Set(connections.data!.flatMap((item) => item.roles))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
    <Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Platform</TableHead><TableHead>Role</TableHead><TableHead>Interface</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => {
      const status = connectionStatus(connection.status)
      const openDetails = () => { setSelected(connection); setTab("overview") }
      return <TableRow key={connection.id}><TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={openDetails}><Marker icon={PlugZap} />{connection.display_name}</button></TableCell><TableCell><Provider value={connection.platform} /></TableCell><TableCell className="text-[var(--ink-soft)]">{roleLabel(connection)}</TableCell><TableCell>{titleCase(connection.interface)}</TableCell><TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell><TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={openDetails}>{connectionAction(connection.status)} <ChevronRight className="size-3.5" /></Button></div></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}

type ConnectionSetupProps = {
  onClose: () => void
  playbooks: Playbook[]
  connections: Connection[]
  onChanged: () => Promise<void>
  onCreated?: (connection: Connection) => Promise<void>
  initialRoles?: ConnectionRole[]
}

const providerCapabilities = ["provider.listCredentialMetadata", "provider.createCredential", "provider.getCredentialStatus", "provider.revokeCredential", "provider.testCredential"]
export function ConnectionSetup({ onClose, playbooks, connections, onChanged, onCreated, initialRoles = [] }: ConnectionSetupProps) {
  const [integration, setIntegration] = useState<IntegrationKind | null>(() => connectionCallbackIntegration())
  const requestedProvider = initialRoles.length === 1 && initialRoles[0] === "provider"
  const requestedGoogle = initialRoles.some((role) => role === "runtime" || role === "secret-store")
  const requestedIncident = initialRoles.length === 1 && initialRoles[0] === "incident"
  const visible: IntegrationKind[] = requestedIncident
    ? ["github"]
    : requestedProvider
    ? ["custom-api", "computer-use"]
    : requestedGoogle
      ? ["google-cloud"]
      : ["github", "google-cloud", "custom-api", "computer-use"]
  const connected = {
    github: connections.some((item) => item.platform === "github" && item.roles.includes("incident")),
    "google-cloud": connections.some((item) => item.platform === "google-cloud") || (connections.some((item) => item.platform === "cloud-run") && connections.some((item) => item.platform === "google-secret-manager")),
    "custom-api": connections.some((item) => item.interface === "api" && item.roles.includes("provider") && item.http !== null),
    "computer-use": connections.some((item) => item.interface === "browser"),
  }

  if (integration === "custom-api") return <CustomApiSetup {...{ onClose, onChanged, onCreated, connections, playbooks }} onBack={() => setIntegration(null)} />
  if (integration === "google-cloud") return <GoogleCloudSetup {...{ onClose, onChanged, onCreated, initialRoles }} onBack={() => setIntegration(null)} />
  if (integration === "computer-use") return <ComputerUseSetup {...{ onClose, onChanged, onCreated, connections, playbooks }} onBack={() => setIntegration(null)} />
  if (integration === "github") return <GitHubSetup {...{ onClose, onChanged, onCreated, connections }} onBack={() => setIntegration(null)} />

  return <div className="page max-w-[960px]">
    <PageHeader eyebrow="Inventory / Connections" title="Add connection" onBack={onClose} />
    <IntegrationGrid visible={visible} connected={connected} onSelect={setIntegration} />
  </div>
}

function CustomApiSetup({ onClose, onBack, onChanged, onCreated, connections, playbooks }: Omit<ConnectionSetupProps, "initialRoles"> & { onBack: () => void }) {
  const [step, setStep] = useState(0)
  const [provider, setProvider] = useState("")
  const [adapter, setAdapter] = useState<HttpProviderApi | null>(null)
  const [adapterName, setAdapterName] = useState("")
  const [adapterError, setAdapterError] = useState("")
  const [secretStoreId, setSecretStoreId] = useState("")
  const [secretResource, setSecretResource] = useState("")
  const [authorizationReference, setAuthorizationReference] = useState("")
  const [addingSecretStore, setAddingSecretStore] = useState(false)
  const secretStores = connections.filter((item) => item.status === "ready" && item.roles.includes("secret-store") && item.interface === "api")
  const secretResources = useQuery({ queryKey: ["secret-resources", secretStoreId], queryFn: () => api.getSecretResources(secretStoreId), enabled: Boolean(secretStoreId) })
  const secretVersions = useQuery({ queryKey: ["secret-versions", secretStoreId, secretResource], queryFn: () => api.getSecretVersions(secretStoreId, secretResource), enabled: Boolean(secretStoreId && secretResource) })
  const create = useMutation({ mutationFn: (input: CreateConnectionInput) => api.createConnection(input) })

  useEffect(() => { if (!secretStoreId && secretStores[0]) setSecretStoreId(secretStores[0].id) }, [secretStoreId, secretStores])
  useEffect(() => { setSecretResource(""); setAuthorizationReference("") }, [secretStoreId])
  useEffect(() => { setAuthorizationReference("") }, [secretResource])

  async function loadAdapter(file: File | undefined) {
    if (!file) return
    try {
      const parsed = parseProviderAdapter(await file.text())
      setAdapter(parsed)
      setAdapterName(file.name)
      setAdapterError("")
    } catch (error) {
      setAdapter(null)
      setAdapterName("")
      setAdapterError(error instanceof Error ? error.message : "API definition is invalid")
    }
  }

  function canContinue() {
    if (step === 0) return Boolean(provider.trim() && adapter)
    if (step === 1) return Boolean(secretStoreId && secretResource && authorizationReference)
    return true
  }

  async function submit() {
    if (!adapter) return
    const timestamp = new Date().toISOString()
    const platform = slug(provider)
    const result = await create.mutateAsync({ connection: {
      id: identifier("conn"), organisation_id: activeOrganisationId(), platform, display_name: `${provider.trim()} API`, roles: ["provider"], interface: "api", authorization: "api-key", authorization_reference: authorizationReference,
      capabilities: providerCapabilities, allowed_resources: [`${platform}:credentials:*`], http: adapter, playbook_id: null, playbook_version_id: null, status: "setup-required", authenticated_at: null, authorization_expires_at: null, last_validated_at: null,
      region: "global", created_at: timestamp, updated_at: timestamp, revision: 0,
    } })
    await onChanged()
    if (onCreated) await onCreated(result)
    else onClose()
  }

  if (addingSecretStore) return <ConnectionSetup initialRoles={["secret-store"]} playbooks={playbooks} connections={connections} onClose={() => setAddingSecretStore(false)} onChanged={onChanged} onCreated={async (connection) => { setSecretStoreId(connection.id); setAddingSecretStore(false) }} />

  return <SetupPage eyebrow="Inventory / Connections" title="Custom API" steps={["API", "Access", "Review"]} current={step} onBack={() => setStep((value) => value - 1)} onExit={onBack} onCancel={onClose} error={adapterError || create.error?.message || secretResources.error?.message || secretVersions.error?.message} primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>Continue</Button> : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Testing…" : "Test connection"}</Button>}>
    {step === 0 && <FormGrid>
      <Field label="Provider"><input className={formControl} value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="Provider name" /></Field>
      <Field label="API definition"><label className="focus-ring flex h-11 cursor-pointer items-center rounded-xl border border-[var(--border)] bg-white px-3.5 text-[10px] font-medium text-[var(--ink-soft)]"><Upload className="mr-2 size-3.5" />{adapterName || "Choose JSON definition"}<input className="sr-only" type="file" accept="application/json,.json" onChange={(event) => void loadAdapter(event.target.files?.[0])} /></label></Field>
    </FormGrid>}
    {step === 1 && <FormGrid>
      <ResourceSelect label="Access secret store" value={secretStoreId} onChange={setSecretStoreId} addLabel="Add connection" onAdd={() => setAddingSecretStore(true)}><option value="">Select secret store</option>{secretStores.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</ResourceSelect>
      <Field label="Access secret"><SelectControl value={secretResource} onChange={(event) => setSecretResource(event.target.value)} disabled={!secretStoreId || secretResources.isLoading}><option value="">Select secret</option>{(secretResources.data ?? []).map((item) => <option key={item.reference} value={item.reference}>{item.display_name}</option>)}</SelectControl></Field>
      <Field label="Enabled version"><SelectControl value={authorizationReference} onChange={(event) => setAuthorizationReference(event.target.value)} disabled={!secretResource || secretVersions.isLoading}><option value="">Select version</option>{(secretVersions.data ?? []).filter((item) => item.state === "ENABLED").map((item) => <option key={item.reference} value={item.reference}>{item.reference.split("/").at(-1)}</option>)}</SelectControl></Field>
    </FormGrid>}
    {step === 2 && <div className="grid gap-5 lg:grid-cols-2"><Section title="API" onEdit={() => setStep(0)}><DetailList><Detail label="Provider"><Provider value={slug(provider)} /></Detail><Detail label="Definition">{adapterName}</Detail><Detail label="Base URL">{adapter?.base_url}</Detail><Detail label="Operations">List, create, test, revoke</Detail><Detail label="Response mappings">Credential ID and secret value</Detail></DetailList></Section><Section title="Access" onEdit={() => setStep(1)}><DetailList><Detail label="Authentication">API key</Detail><Detail label="Access secret">{secretResource.split("/").at(-1)}</Detail><Detail label="Validation">Operations and response mappings</Detail></DetailList></Section></div>}
  </SetupPage>
}

function GoogleCloudSetup({ onClose, onBack, onChanged, onCreated }: Omit<ConnectionSetupProps, "playbooks" | "connections"> & { onBack: () => void }) {
  const [step, setStep] = useState(0)
  const [projects, setProjects] = useState<GoogleCloudProject[]>([])
  const [sessionId, setSessionId] = useState("")
  const [projectId, setProjectId] = useState("")
  const [automationIdentity, setAutomationIdentity] = useState("")
  const [prepared, setPrepared] = useState<{ connection: Connection; grant_command: string } | null>(null)
  const selectedProject = projects.find((item) => item.project_id === projectId)
  const begin = useMutation({
    mutationFn: () => api.beginGoogleCloudOnboarding(),
    onSuccess: (value) => {
      sessionStorage.setItem("uumi.google-cloud", JSON.stringify(value))
      window.location.assign(value.authorization_url)
    },
  })
  const discover = useMutation({ mutationFn: ({ saved, code, state }: { saved: GoogleCloudOnboardingResponse; code: string; state: string }) => api.completeGoogleCloudOnboarding(saved.session.id, { state, pkce_verifier: saved.pkce_verifier, code }) })
  const prepare = useMutation({ mutationFn: async () => {
    if (!selectedProject) throw new Error("Select a Google Cloud project")
    if (!sessionId) throw new Error("Google Cloud discovery session is unavailable")
    return api.prepareGoogleCloudConnection(sessionId, { project_id: selectedProject.project_id, automation_identity: automationIdentity })
  } })
  const verify = useMutation({ mutationFn: () => {
    if (!prepared || !sessionId) throw new Error("Google Cloud access is not ready to verify")
    return api.verifyGoogleCloudConnection(sessionId, prepared.connection.revision)
  } })
  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search)
    const code = parameters.get("code")
    const state = parameters.get("state")
    if (!parameters.has("google_cloud") || !code || !state) return
    const raw = sessionStorage.getItem("uumi.google-cloud")
    if (!raw) return
    try {
      const saved = JSON.parse(raw) as GoogleCloudOnboardingResponse
      discover.mutate({ saved, code, state }, {
        onSuccess: (value) => {
          setProjects(value.projects)
          setSessionId(value.session.id)
          sessionStorage.removeItem("uumi.google-cloud")
          window.history.replaceState({}, "", window.location.pathname)
        },
      })
    } catch {
      sessionStorage.removeItem("uumi.google-cloud")
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setAutomationIdentity(selectedProject?.service_accounts.length === 1 ? selectedProject.service_accounts[0].email : "")
  }, [projectId]) // eslint-disable-line react-hooks/exhaustive-deps
  const canContinue = Boolean(selectedProject?.services.length && automationIdentity)

  async function continueSetup() {
    const result = await prepare.mutateAsync()
    setPrepared(result)
    setStep(1)
  }

  async function submit() {
    const result = await verify.mutateAsync()
    await onChanged()
    if (onCreated) await onCreated(result)
    else onClose()
  }

  if (!projects.length) return <ConnectPage eyebrow="Inventory / Connections" title="Google Cloud" onBack={onBack} onClose={onClose} error={(begin.error ?? discover.error)?.message} action={<Button onClick={() => begin.mutate()} disabled={begin.isPending || discover.isPending}>{begin.isPending || discover.isPending ? "Connecting…" : "Connect"}</Button>}><IntegrationMark kind="google-cloud" /></ConnectPage>

  return <SetupPage eyebrow="Inventory / Connections" title="Google Cloud" steps={["Project", "Review"]} current={step} onBack={() => setStep((value) => value - 1)} onExit={onBack} onCancel={onClose} error={(prepare.error ?? verify.error)?.message} primary={step === 0 ? <Button onClick={continueSetup} disabled={!canContinue || prepare.isPending}>{prepare.isPending ? "Preparing…" : "Continue"}</Button> : <Button onClick={submit} disabled={verify.isPending}>{verify.isPending ? "Verifying…" : "Verify access"}</Button>}>
    {step === 0 && <FormGrid><Field label="Project"><SelectControl value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select project</option>{projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.display_name}</option>)}</SelectControl></Field><Field label="Automation identity"><SelectControl value={automationIdentity} onChange={(event) => setAutomationIdentity(event.target.value)} disabled={!selectedProject}><option value="">Select identity</option>{(selectedProject?.service_accounts ?? []).map((account) => <option key={account.email} value={account.email}>{account.display_name}</option>)}</SelectControl></Field></FormGrid>}
    {step === 1 && <div className="space-y-5"><div className="grid gap-5 lg:grid-cols-2"><Section title="Google Cloud" onEdit={() => { setPrepared(null); setStep(0) }}><DetailList><Detail label="Project">{selectedProject?.display_name}</Detail><Detail label="Cloud Run services">{selectedProject?.services.length}</Detail></DetailList></Section><Section title="Access" onEdit={() => { setPrepared(null); setStep(0) }}><DetailList><Detail label="Automation identity">{selectedProject?.service_accounts.find((item) => item.email === automationIdentity)?.display_name}</Detail><Detail label="Authorization">Workload identity</Detail></DetailList></Section></div><Field label="Grant access"><div className="flex items-center gap-3"><code className="min-w-0 flex-1 overflow-x-auto rounded-xl border border-[var(--border)] bg-white px-3.5 py-3 text-[9px] text-[var(--ink)]">{prepared?.grant_command}</code><Button variant="secondary" onClick={() => void navigator.clipboard.writeText(prepared?.grant_command ?? "")}>Copy</Button></div></Field></div>}
  </SetupPage>
}

function ComputerUseSetup({ onClose, onBack, onChanged, onCreated, playbooks }: ConnectionSetupProps & { onBack: () => void }) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [playbookVersion, setPlaybookVersion] = useState(playbooks.find((item) => item.active_version_id)?.active_version_id ?? "")
  const [created, setCreated] = useState<Connection | null>(null)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const chosenPlaybook = playbooks.find((item) => item.active_version_id === playbookVersion)
  const playbookDetail = useQuery({ queryKey: ["playbooks", chosenPlaybook?.id], queryFn: () => api.getPlaybook(chosenPlaybook!.id), enabled: Boolean(chosenPlaybook) })
  const create = useMutation({ mutationFn: (input: CreateConnectionInput) => api.createConnection(input) })
  const open = useMutation({ mutationFn: (id: string) => api.beginBrowserSetup(id) })
  const definition = playbookDetail.data?.active_version?.definition

  async function submit() {
    if (!chosenPlaybook || !definition) return
    const timestamp = new Date().toISOString()
    const result = await create.mutateAsync({ connection: {
      id: identifier("conn"), organisation_id: activeOrganisationId(), platform: chosenPlaybook.platform, display_name: chosenPlaybook.name, roles: ["provider"], interface: "browser", authorization: "browser-session", authorization_reference: null,
      capabilities: ["browser.authenticate", "browser.execute", "browser.secureCapture"], allowed_resources: definition.allowed_domains, http: null, playbook_id: null, playbook_version_id: null, status: "setup-required", authenticated_at: null, authorization_expires_at: null, last_validated_at: null,
      region: "global", created_at: timestamp, updated_at: timestamp, revision: 0,
    }, playbook_id: chosenPlaybook.id, playbook_version_id: playbookVersion })
    await onChanged()
    setCreated(result)
  }

  async function openBrowser() {
    if (!created) return
    const setup = await open.mutateAsync(created.id)
    const fragment = new URLSearchParams({ organisation_id: created.organisation_id, setup_id: setup.session.id, token: setup.token })
    window.open(`${setup.gateway_url}#${fragment}`, "_blank", "noopener,noreferrer")
  }

  async function finish() {
    if (!created) return
    const refreshed = await api.getConnection(created.id)
    if (onCreated) await onCreated(refreshed)
    else onClose()
  }

  if (creatingPlaybook) return <PlaybookSetup onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => { await queryClient.invalidateQueries({ queryKey: ["playbooks"] }); setPlaybookVersion(playbook.active_version_id ?? ""); setCreatingPlaybook(false) }} />
  if (created) return <SuccessPage eyebrow="Inventory / Connections" title="Computer Use ready" onBack={finish} actions={<><Button variant="secondary" onClick={finish}>Finish later</Button><Button onClick={openBrowser} disabled={open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button></>}><DetailList><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Status">Authentication required</Detail></DetailList>{open.error && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{open.error.message}</div>}</SuccessPage>

  const canContinue = Boolean(playbookVersion && definition?.allowed_domains.length)
  return <SetupPage eyebrow="Inventory / Connections" title="Computer Use" steps={["Setup", "Review"]} current={step} onBack={() => setStep((value) => value - 1)} onExit={onBack} onCancel={onClose} error={create.error?.message || playbookDetail.error?.message} primary={step === 0 ? <Button onClick={() => setStep(1)} disabled={!canContinue}>Continue</Button> : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Creating…" : "Create connection"}</Button>}>
    {step === 0 && <div className="space-y-5"><p className="text-[10px] leading-5 text-[var(--ink-muted)]">Computer Use follows a playbook to rotate credentials in the vendor dashboard.</p><ResourceSelect label="Playbook" value={playbookVersion} onChange={setPlaybookVersion} addLabel="Add playbook" onAdd={() => setCreatingPlaybook(true)}><option value="">Select playbook</option>{playbooks.filter((item) => item.active_version_id).map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</ResourceSelect></div>}
    {step === 1 && <Section title="Computer Use" onEdit={() => setStep(0)}><DetailList><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Platform"><Provider value={chosenPlaybook?.platform ?? ""} /></Detail><Detail label="Access">Secure browser</Detail></DetailList></Section>}
  </SetupPage>
}

type SavedGitHubOnboarding = GitHubOnboardingResponse & { installation_id?: number }

function GitHubSetup({ onClose, onBack, onChanged, onCreated, connections }: Pick<ConnectionSetupProps, "onClose" | "onChanged" | "onCreated" | "connections"> & { onBack: () => void }) {
  const [step, setStep] = useState(0)
  const [discovery, setDiscovery] = useState<GitHubDiscoveryResponse | null>(null)
  const [callbackError, setCallbackError] = useState("")
  const begin = useMutation({
    mutationFn: () => api.beginGitHubOnboarding(),
    onSuccess: (value) => {
      sessionStorage.setItem("uumi.github", JSON.stringify(value))
      window.location.assign(value.installation_url)
    },
  })
  const discover = useMutation({
    mutationFn: ({ saved, code, state, installationId }: { saved: SavedGitHubOnboarding; code: string; state: string; installationId: number }) => api.discoverGitHubOnboarding(saved.session.id, { state, pkce_verifier: saved.pkce_verifier, code, installation_id: installationId }),
  })
  const complete = useMutation({ mutationFn: async () => {
    if (!discovery) throw new Error("GitHub repositories are unavailable")
    const completed = await api.completeGitHubOnboarding(discovery.session.id)
    const reference = `oauth://github/installation/${completed.installation.installation_id}`
    const existing = connections.find((item) => item.authorization_reference === reference)
    if (existing) return existing
    const timestamp = new Date().toISOString()
    return api.createConnection({ connection: {
      id: `conn_github_${completed.installation.installation_id}`,
      organisation_id: activeOrganisationId(),
      platform: "github",
      display_name: `GitHub · ${completed.installation.account_login}`,
      roles: ["incident"],
      interface: "api",
      authorization: "oauth",
      authorization_reference: reference,
      capabilities: ["incident.verifyWebhook", "incident.readFinding", "repository.resolveContext"],
      allowed_resources: completed.repositories.map((item) => item.full_name),
      http: null,
      playbook_id: null,
      playbook_version_id: null,
      status: completed.installation.ready ? "ready" : "setup-required",
      authenticated_at: timestamp,
      authorization_expires_at: null,
      last_validated_at: completed.installation.ready ? timestamp : null,
      region: "global",
      created_at: timestamp,
      updated_at: timestamp,
      revision: 0,
    } })
  } })

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search)
    const callback = parameters.has("github") || parameters.has("installation_id") || (parameters.has("code") && sessionStorage.getItem("uumi.github"))
    if (!callback) return
    const raw = sessionStorage.getItem("uumi.github")
    if (!raw) {
      setCallbackError("GitHub connection session is unavailable")
      return
    }
    try {
      const saved = JSON.parse(raw) as SavedGitHubOnboarding
      const installationId = Number(parameters.get("installation_id") ?? saved.installation_id)
      const code = parameters.get("code")
      const state = parameters.get("state")
      if (!Number.isInteger(installationId) || installationId <= 0) {
        setCallbackError("GitHub did not return an installation")
        return
      }
      if (!code) {
        const continued = { ...saved, installation_id: installationId }
        sessionStorage.setItem("uumi.github", JSON.stringify(continued))
        window.location.assign(saved.authorization_url)
        return
      }
      if (!state) {
        setCallbackError("GitHub authorization is incomplete")
        return
      }
      discover.mutate({ saved, code, state, installationId }, { onSuccess: (value) => {
        setDiscovery(value)
        sessionStorage.removeItem("uumi.github")
        window.history.replaceState({}, "", window.location.pathname)
      } })
    } catch {
      sessionStorage.removeItem("uumi.github")
      setCallbackError("GitHub connection session is invalid")
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    const connection = await complete.mutateAsync()
    await onChanged()
    if (onCreated) await onCreated(connection)
    else onClose()
  }

  if (!discovery) {
    const returning = Boolean(sessionStorage.getItem("uumi.github") && connectionCallbackIntegration() === "github")
    return <ConnectPage eyebrow="Inventory / Connections" title="GitHub" onBack={onBack} onClose={onClose} error={callbackError || begin.error?.message || discover.error?.message} action={<Button onClick={() => begin.mutate()} disabled={begin.isPending || discover.isPending || returning}>{begin.isPending || discover.isPending || returning ? "Connecting…" : "Connect"}</Button>}><IntegrationMark kind="github" /></ConnectPage>
  }

  return <SetupPage eyebrow="Inventory / Connections" title="GitHub" steps={["Repositories", "Review"]} current={step} onBack={() => setStep(0)} onExit={onBack} onCancel={onClose} error={complete.error?.message} primary={step === 0 ? <Button onClick={() => setStep(1)}>Continue</Button> : <Button onClick={submit} disabled={complete.isPending}>{complete.isPending ? "Connecting…" : "Connect"}</Button>}>
    {step === 0 && <div className="overflow-hidden rounded-xl border border-[var(--border)]">
      <div className="grid grid-cols-[minmax(0,1fr)_140px] gap-4 border-b border-[var(--border-soft)] px-4 py-3 text-[9px] font-semibold text-[var(--ink-muted)]"><span>Repository</span><span>Status</span></div>
      {discovery.repositories.map((repository) => <div key={repository.repository_id} className="grid grid-cols-[minmax(0,1fr)_140px] items-center gap-4 border-b border-[var(--border-soft)] px-4 py-3 last:border-b-0"><div className="truncate text-[11px] font-semibold text-[var(--ink)]">{repository.full_name}</div><span className="text-[10px] text-[var(--ink-soft)]">{repository.secret_scanning === "enabled" ? "Ready" : "Action required"}</span></div>)}
    </div>}
    {step === 1 && <div className="grid gap-5 lg:grid-cols-2"><Section title="GitHub" onEdit={() => setStep(0)}><DetailList><Detail label="Account">{discovery.installation.account_login}</Detail><Detail label="Repositories">{discovery.repositories.length}</Detail></DetailList></Section><Section title="Access" onEdit={() => setStep(0)}><DetailList><Detail label="Webhook">{discovery.installation.ready ? "Verified" : "Action required"}</Detail><Detail label="Secret scanning">{discovery.repositories.every((item) => item.secret_scanning === "enabled") ? "Ready" : "Action required"}</Detail></DetailList></Section></div>}
  </SetupPage>
}
