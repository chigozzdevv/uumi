import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, ExternalLink, PlugZap, Plus } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, Fieldset, FormGrid, ResourceSelect, SelectControl, SetupPage, SuccessPage, formControl } from "../components/workspace"
import type { Connection, ConnectionAuthorization, ConnectionInterface, ConnectionRole, Playbook } from "../types"
import { api, type CreateConnectionInput } from "../lib/api"
import { connectionAction, connectionStatus, formatDate, titleCase } from "../lib/format"
import { PlaybookSetup } from "./playbooks"

const setupSteps = ["Connection", "Access", "Review"]
const organisationRegion = "us-central1"

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

function roleLabel(connection: Connection) {
  return connection.roles.map(titleCase).join(", ")
}

function suggestedCapabilities(roles: ConnectionRole[], platform: string) {
  const values: Record<ConnectionRole, string[]> = {
    provider: ["provider.listCredentialMetadata", "provider.createCredential", "provider.getCredentialStatus", "provider.revokeCredential"],
    runtime: platform === "cloud-run" ? ["runtime.listServices", "runtime.inspectSecretBindings", "runtime.deployCandidate", "runtime.shiftTraffic", "runtime.rollback"] : [],
    "secret-store": ["secretStore.getVersion", "secretStore.testConsumerAccess", "secretStore.disableVersion", "secretStore.destroyVersion"],
    telemetry: ["telemetry.queryHealth", "telemetry.queryCredentialUsage"],
    incident: ["incident.receive", "incident.updateStatus"],
  }
  return [...new Set(roles.flatMap((role) => values[role]))].join(", ")
}

export function ConnectionsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [role, setRole] = useState("all")
  const [selected, setSelected] = useState<Connection | null>(null)
  const [tab, setTab] = useState<"overview" | "access">("overview")
  const [sessionContainer, setSessionContainer] = useState("")
  const [selectedPlaybookVersion, setSelectedPlaybookVersion] = useState("")
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const [editName, setEditName] = useState("")
  const [editResources, setEditResources] = useState("")
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
  const open = useMutation({ mutationFn: ({ id, container }: { id: string; container: string }) => api.beginBrowserSetup(id, container) })
  const updateConnection = useMutation({
    mutationFn: () => api.updateConnection(currentSelected!.id, { expected_revision: currentSelected!.revision, display_name: editName.trim(), allowed_resources: editResources.split(",").map((item) => item.trim()).filter(Boolean) }),
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
    if (currentSelected?.interface !== "browser") return
    setSessionContainer(currentSelected.authorization_reference?.replace(/\/versions\/[^/]+$/, "") ?? "")
    setSelectedPlaybookVersion(browserPlaybooks[0]?.active_version_id ?? "")
  }, [currentSelected?.id, currentSelected?.authorization_reference, currentSelected?.interface]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const refresh = () => { void queryClient.invalidateQueries({ queryKey: ["connections"] }) }
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [queryClient])

  async function openBrowser(connection: Connection) {
    const setup = await open.mutateAsync({ id: connection.id, container: sessionContainer.trim() })
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

  if (creating) return <ConnectionSetup onClose={() => setCreating(false)} playbooks={playbooks.data!} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} />

  if (creatingPlaybook && currentSelected) return <PlaybookSetup initialPlatform={currentSelected.platform} onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => {
    await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    setSelectedPlaybookVersion(playbook.active_version_id ?? "")
    setCreatingPlaybook(false)
  }} />

  if (currentSelected) return <div className="page">
    <PageHeader eyebrow="Management / Connections" title={currentSelected.display_name} onBack={() => setSelected(null)} actions={<>{currentSelected.interface === "browser" && currentSelected.playbook_version_id && <Button onClick={() => openBrowser(currentSelected)} disabled={!sessionContainer.trim() || open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.display_name); setEditResources(currentSelected.allowed_resources.join(", ")); updateConnection.reset(); archiveConnection.reset(); setEditing(true) }}>Edit</Button></>} />
    <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "access", label: currentSelected.interface === "browser" ? "Browser access" : "Access" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Roles">{roleLabel(currentSelected)}</Detail><Detail label="Interface">{titleCase(currentSelected.interface)}</Detail><Detail label="Authorization">{titleCase(currentSelected.authorization)}</Detail><Detail label="Status"><Badge variant={connectionStatus(currentSelected.status).variant}>{connectionStatus(currentSelected.status).label}</Badge></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "access" && (currentSelected.interface === "browser" ? <div className="max-w-[620px] space-y-5"><DetailList><Detail label="Playbook version">{currentSelected.playbook_version_id ?? "Not attached"}</Detail><Detail label="Session expires">{currentSelected.authorization_expires_at ? formatDate(currentSelected.authorization_expires_at, true) : "Authentication required"}</Detail></DetailList>{currentSelected.playbook_version_id ? <Field label="Browser session location"><input className={formControl} value={sessionContainer} onChange={(event) => setSessionContainer(event.target.value)} placeholder="projects/project/secrets/platform-session" /></Field> : <div className="space-y-3"><ResourceSelect label="Playbook" value={selectedPlaybookVersion} onChange={setSelectedPlaybookVersion} addLabel="Add playbook" onAdd={() => setCreatingPlaybook(true)}><option value="">Select playbook</option>{browserPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</ResourceSelect><Button onClick={() => { const playbook = browserPlaybooks.find((item) => item.active_version_id === selectedPlaybookVersion); if (playbook) attach.mutate({ connection: currentSelected, playbook }) }} disabled={!selectedPlaybookVersion || attach.isPending}>Attach playbook</Button></div>}</div> : <DetailList><Detail label="Allowed resources">{currentSelected.allowed_resources.join(", ")}</Detail><Detail label="Capabilities">{currentSelected.capabilities.length}</Detail><Detail label="Last validated">{currentSelected.last_validated_at ? formatDate(currentSelected.last_validated_at, true) : "Never"}</Detail></DetailList>)}
    </DetailCard>
    {(attach.error || open.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(attach.error ?? open.error)?.message}</div>}
    <ManageResourceModal isOpen={editing} onClose={() => setEditing(false)} title="Edit connection" resourceLabel="connection" onSave={() => updateConnection.mutate()} onDelete={() => archiveConnection.mutate()} dependencies={[
      { label: "Credentials", items: graph.data!.credentials.filter((credential) => currentSelected.id === credential.connection_id || currentSelected.id === credential.secret_store_connection_id).map((credential) => credential.display_name) },
      { label: "Services", items: graph.data!.services.filter((service) => currentSelected.id === service.runtime_connection_id || service.telemetry_connection_ids.includes(currentSelected.id)).map((service) => service.display_name) },
    ]} saveDisabled={!editName.trim() || !editResources.trim() || (editName.trim() === currentSelected.display_name && editResources.split(",").map((item) => item.trim()).filter(Boolean).join(",") === currentSelected.allowed_resources.join(","))} saving={updateConnection.isPending} deleting={archiveConnection.isPending} error={(updateConnection.error ?? archiveConnection.error)?.message}>
      <div className="space-y-4"><Field label="Connection name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field><Field label={currentSelected.interface === "browser" ? "Allowed domains" : "Allowed resources"}><input className={formControl} value={editResources} onChange={(event) => setEditResources(event.target.value)} /></Field></div>
    </ManageResourceModal>
  </div>

  return <div className="page">
    <PageHeader eyebrow="Management" title="Connections" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add connection</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search connections or platforms" onClear={() => { setSearch(""); setRole("all") }} filters={[{ label: "Role", value: role, defaultValue: "all", onChange: (event) => setRole(event.target.value), children: <><option value="all">All roles</option>{[...new Set(connections.data!.flatMap((item) => item.roles))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
    <Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Platform</TableHead><TableHead>Role</TableHead><TableHead>Interface</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => {
      const status = connectionStatus(connection.status)
      const openDetails = () => { setSelected(connection); setTab("overview") }
      return <TableRow key={connection.id}><TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={openDetails}><Marker icon={PlugZap} />{connection.display_name}</button></TableCell><TableCell><Provider value={connection.platform} /></TableCell><TableCell className="text-[var(--ink-soft)]">{roleLabel(connection)}</TableCell><TableCell>{titleCase(connection.interface)}</TableCell><TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell><TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={openDetails}>{connectionAction(connection.status)} <ChevronRight className="size-3.5" /></Button></div></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}

export function ConnectionSetup({ onClose, playbooks, onChanged, onCreated, initialRoles = ["provider"], initialPlatform = "" }: { onClose: () => void; playbooks: Playbook[]; onChanged: () => Promise<void>; onCreated?: (connection: Connection) => Promise<void>; initialRoles?: ConnectionRole[]; initialPlatform?: string }) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [connectionRoles, setConnectionRoles] = useState<ConnectionRole[]>(initialRoles)
  const [connectionInterface, setConnectionInterface] = useState<ConnectionInterface>("api")
  const [authorization, setAuthorization] = useState<ConnectionAuthorization>("oauth")
  const [name, setName] = useState("")
  const [platform, setPlatform] = useState(initialPlatform)
  const [authorizationReference, setAuthorizationReference] = useState("")
  const [resources, setResources] = useState("")
  const [capabilities, setCapabilities] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [requestScheme, setRequestScheme] = useState<"bearer" | "header" | "basic">("bearer")
  const [requestHeader, setRequestHeader] = useState("Authorization")
  const [requestPrefix, setRequestPrefix] = useState("Bearer ")
  const [listPath, setListPath] = useState("/credentials")
  const [createPath, setCreatePath] = useState("/credentials")
  const [revokePath, setRevokePath] = useState("/credentials/{provider_id}")
  const [playbookVersion, setPlaybookVersion] = useState("")
  const [sessionContainer, setSessionContainer] = useState("")
  const [created, setCreated] = useState<Connection | null>(null)
  const [creatingPlaybook, setCreatingPlaybook] = useState(false)
  const create = useMutation({ mutationFn: (input: CreateConnectionInput) => api.createConnection(input), onSuccess: onChanged })
  const open = useMutation({ mutationFn: ({ id, container }: { id: string; container: string }) => api.beginBrowserSetup(id, container) })

  const matchingPlaybooks = playbooks.filter((item) => item.platform === platform && item.active_version_id)
  const chosenPlaybook = matchingPlaybooks.find((item) => item.active_version_id === playbookVersion)

  useEffect(() => {
    if (connectionInterface === "browser") {
      setConnectionRoles(["provider"])
      setAuthorization("browser-session")
      setCapabilities("browser.authenticate, browser.execute, browser.secureCapture")
      setAuthorizationReference("")
    } else if (authorization === "browser-session") {
      setAuthorization("oauth")
    }
  }, [authorization, connectionInterface])

  useEffect(() => {
    if (connectionRoles.includes("provider") && authorization === "workload-identity") setAuthorization("oauth")
    if (!connectionRoles.includes("provider") && connectionInterface === "browser") setConnectionInterface("api")
  }, [authorization, connectionInterface, connectionRoles])

  useEffect(() => {
    if (connectionInterface === "api") setCapabilities(suggestedCapabilities(connectionRoles, platform))
  }, [connectionInterface, connectionRoles, platform])

  useEffect(() => {
    if (authorization === "oauth") {
      setRequestScheme("bearer")
      setRequestHeader("Authorization")
      setRequestPrefix("Bearer ")
    } else if (authorization === "api-key" && requestScheme === "bearer") {
      setRequestScheme("header")
      setRequestHeader("X-API-Key")
      setRequestPrefix("")
    }
  }, [authorization]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setPlaybookVersion(matchingPlaybooks[0]?.active_version_id ?? "")
  }, [platform, playbooks]) // eslint-disable-line react-hooks/exhaustive-deps

  function canContinue() {
    if (step === 0) return Boolean(name.trim() && platform.trim() && connectionRoles.length)
    if (step === 1) return Boolean(resources.trim()) && (connectionInterface === "browser" ? Boolean(playbookVersion && sessionContainer.trim()) : Boolean(authorizationReference.trim() && (!connectionRoles.includes("provider") || (baseUrl.trim() && requestHeader.trim()))))
    return true
  }

  async function submit() {
    const id = identifier("conn")
    const timestamp = new Date().toISOString()
    const isProviderApi = connectionRoles.includes("provider") && connectionInterface === "api"
    const operation = (method: "GET" | "POST" | "DELETE", path: string) => ({ method, path, success_statuses: method === "POST" ? [200, 201] : method === "DELETE" ? [200, 204] : [200], query: {}, body: {}, list_items: method === "GET" ? "items" : null, provider_id_field: method === "DELETE" ? null : "id", secret_field: method === "POST" ? "secret" : null, name_field: method === "DELETE" ? null : "name" })
    const connection: Connection = {
      id,
      organisation_id: "org_acme",
      platform: platform.trim(),
      display_name: name.trim(),
      roles: connectionInterface === "browser" ? ["provider"] : connectionRoles,
      interface: connectionInterface,
      authorization,
      authorization_reference: connectionInterface === "browser" ? null : authorizationReference.trim(),
      capabilities: capabilities.split(",").map((item) => item.trim()).filter(Boolean),
      allowed_resources: resources.split(",").map((item) => item.trim()).filter(Boolean),
      http: isProviderApi ? { base_url: baseUrl.trim(), auth: { scheme: requestScheme, header: requestHeader.trim(), prefix: requestPrefix || null }, list_credentials: operation("GET", listPath), create_credential: operation("POST", createPath), revoke_credential: operation("DELETE", revokePath) } : null,
      playbook_id: null,
      playbook_version_id: null,
      status: connectionInterface === "browser" ? "setup-required" : "ready",
      authenticated_at: connectionInterface === "browser" ? null : timestamp,
      authorization_expires_at: null,
      last_validated_at: connectionInterface === "browser" ? null : timestamp,
      region: organisationRegion,
      created_at: timestamp,
      updated_at: timestamp,
      revision: 0,
    }
    const result = await create.mutateAsync({ connection, playbook_id: connectionInterface === "browser" ? chosenPlaybook?.id : undefined, playbook_version_id: connectionInterface === "browser" ? playbookVersion : undefined })
    if (connectionInterface === "browser") setCreated(result)
    else if (onCreated) await onCreated(result)
    else onClose()
  }

  async function openBrowser() {
    if (!created) return
    const setup = await open.mutateAsync({ id: created.id, container: sessionContainer.trim() })
    const fragment = new URLSearchParams({ organisation_id: created.organisation_id, setup_id: setup.session.id, token: setup.token })
    window.open(`${setup.gateway_url}#${fragment}`, "_blank", "noopener,noreferrer")
  }

  async function finishBrowserSetup() {
    if (!created) return
    if (onCreated) await onCreated(created)
    else onClose()
  }

  if (creatingPlaybook) return <PlaybookSetup initialPlatform={platform} onClose={() => setCreatingPlaybook(false)} onCreated={async (playbook) => {
    await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    setPlaybookVersion(playbook.active_version_id ?? "")
    setCreatingPlaybook(false)
  }} />

  if (created) return <SuccessPage eyebrow="Management / Connections" title="Connection created" onBack={finishBrowserSetup} actions={<><Button variant="secondary" onClick={finishBrowserSetup}>Finish later</Button><Button onClick={openBrowser} disabled={open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button></>}><DetailList><Detail label="Connection">{created.display_name}</Detail><Detail label="Status">Setup required</Detail><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Session store">Metadata reference saved</Detail></DetailList>{open.error && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{open.error.message}</div>}</SuccessPage>

  const primaryLabel = step < 2 ? "Continue" : "Create connection"
  return <SetupPage eyebrow="Management / Connections" title="Add connection" steps={setupSteps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={create.error?.message} primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>{primaryLabel}</Button> : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Creating…" : primaryLabel}</Button>}>
    {step === 0 && <FormGrid><Field label="Connection name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production platform access" /></Field><Field label="Platform"><input className={formControl} value={platform} onChange={(event) => setPlatform(slug(event.target.value))} placeholder="platform-id" /></Field><Fieldset label="Roles" wide><div className="grid gap-2 sm:grid-cols-3">{(["provider", "runtime", "secret-store", "telemetry", "incident"] as ConnectionRole[]).map((item) => <label key={item} className="flex items-center gap-2 rounded-xl border border-[var(--border)] px-3 py-3 text-[10px] font-medium"><input type="checkbox" checked={connectionRoles.includes(item)} onChange={(event) => setConnectionRoles((current) => event.target.checked ? [...current, item] : current.filter((role) => role !== item))} /> {titleCase(item)}</label>)}</div></Fieldset></FormGrid>}
    {step === 1 && <FormGrid><Field label="Interface"><SelectControl value={connectionInterface} onChange={(event) => setConnectionInterface(event.target.value as ConnectionInterface)}><option value="api">API</option>{connectionRoles.includes("provider") && <option value="browser">Browser</option>}</SelectControl></Field>{connectionInterface === "api" && <Field label="Authorization"><SelectControl value={authorization} onChange={(event) => setAuthorization(event.target.value as ConnectionAuthorization)}><option value="oauth">OAuth</option>{!connectionRoles.includes("provider") && <option value="workload-identity">Workload identity</option>}<option value="api-key">API key</option></SelectControl></Field>}{connectionInterface === "api" ? <><Field label="Authorization reference" wide><input className={formControl} value={authorizationReference} onChange={(event) => setAuthorizationReference(event.target.value)} placeholder="projects/project/secrets/management-access" /></Field>{connectionRoles.includes("provider") && <><Field label="API base URL" wide><input className={formControl} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.platform.example" /></Field><details className="rounded-xl border border-[var(--border-soft)] sm:col-span-2"><summary className="cursor-pointer px-4 py-3 text-[10px] font-semibold">Custom HTTP operations</summary><div className="grid gap-4 border-t border-[var(--border-soft)] p-4 sm:grid-cols-2"><Field label="Request scheme"><SelectControl value={requestScheme} onChange={(event) => setRequestScheme(event.target.value as typeof requestScheme)} disabled={authorization === "oauth"}><option value="bearer">Bearer</option><option value="header">Custom header</option><option value="basic">HTTP Basic</option></SelectControl></Field><Field label="Request header"><input className={formControl} value={requestHeader} onChange={(event) => setRequestHeader(event.target.value)} /></Field><Field label="Header prefix"><input className={formControl} value={requestPrefix} onChange={(event) => setRequestPrefix(event.target.value)} /></Field><Field label="List path"><input className={formControl} value={listPath} onChange={(event) => setListPath(event.target.value)} /></Field><Field label="Create path"><input className={formControl} value={createPath} onChange={(event) => setCreatePath(event.target.value)} /></Field><Field label="Revoke path"><input className={formControl} value={revokePath} onChange={(event) => setRevokePath(event.target.value)} /></Field></div></details></>}</> : <><ResourceSelect label="Playbook" value={playbookVersion} onChange={setPlaybookVersion} addLabel="Add playbook" onAdd={() => setCreatingPlaybook(true)}><option value="">Select playbook</option>{matchingPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</ResourceSelect><Field label="Browser session location"><input className={formControl} value={sessionContainer} onChange={(event) => setSessionContainer(event.target.value)} placeholder="projects/project/secrets/platform-session" /></Field></>}<Field label={connectionInterface === "browser" ? "Allowed domains" : "Allowed resources"} wide><input className={formControl} value={resources} onChange={(event) => setResources(event.target.value)} placeholder={connectionInterface === "browser" ? "*.platform.example" : "projects/project"} /></Field></FormGrid>}
    {step === 2 && <div className="grid gap-5 lg:grid-cols-2"><Section title="Connection" onEdit={() => setStep(0)}><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={platform} /></Detail><Detail label="Roles">{connectionRoles.map(titleCase).join(", ")}</Detail></DetailList></Section><Section title="Access" onEdit={() => setStep(1)}><DetailList><Detail label="Interface">{titleCase(connectionInterface)}</Detail><Detail label="Authorization">{titleCase(authorization)}</Detail><Detail label="Playbook">{connectionInterface === "browser" ? chosenPlaybook?.name : "Not required"}</Detail><Detail label="Resources">{resources}</Detail></DetailList></Section></div>}
  </SetupPage>
}
