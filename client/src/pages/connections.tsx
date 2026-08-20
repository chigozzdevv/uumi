import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, ExternalLink, PlugZap, Plus } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, Fieldset, FormGrid, SetupPage, SuccessPage, formControl } from "../components/workspace"
import type { Connection, ConnectionAuthorization, ConnectionInterface, ConnectionRole, Playbook } from "../types"
import { api, type CreateConnectionInput } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

const setupSteps = ["System", "Access", "Scope", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

function roleLabel(connection: Connection) {
  return connection.roles.map(titleCase).join(", ")
}

function suggestedCapabilities(roles: ConnectionRole[]) {
  const values: Record<ConnectionRole, string[]> = {
    provider: ["provider.listCredentialMetadata", "provider.createCredential", "provider.getCredentialStatus", "provider.revokeCredential"],
    runtime: ["runtime.inspectSecretBindings", "runtime.deployCandidate", "runtime.shiftTraffic", "runtime.rollback"],
    "secret-store": ["secretStore.getVersion", "secretStore.disableVersion", "secretStore.destroyVersion"],
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
  const [sessionContainer, setSessionContainer] = useState("")
  const [selectedPlaybookVersion, setSelectedPlaybookVersion] = useState("")
  const [creating, setCreating] = useState(false)
  const [connections, playbooks] = useQueries({ queries: [
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
  ] })
  const currentSelected = selected ? connections.data?.find((item) => item.id === selected.id) ?? selected : null
  const browserPlaybooks = (playbooks.data ?? []).filter((item) => item.platform === currentSelected?.platform && item.active_version_id)
  const attach = useMutation({
    mutationFn: ({ connection, playbook }: { connection: Connection; playbook: Playbook }) => api.attachPlaybook(connection, playbook.id, playbook.active_version_id!),
    onSuccess: async (connection) => {
      setSelected(connection)
      await queryClient.invalidateQueries({ queryKey: ["connections"] })
    },
  })
  const open = useMutation({ mutationFn: ({ id, container }: { id: string; container: string }) => api.beginBrowserSetup(id, container) })

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
  if ([connections, playbooks].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [connections, playbooks].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  if (creating) return <ConnectionSetup onClose={() => setCreating(false)} playbooks={playbooks.data!} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} />

  if (currentSelected) return <div className="page">
    <PageHeader eyebrow="Management / Connections" title={currentSelected.display_name} onBack={() => setSelected(null)} actions={currentSelected.interface === "browser" && currentSelected.playbook_version_id ? <Button onClick={() => openBrowser(currentSelected)} disabled={!sessionContainer.trim() || open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button> : undefined} />
    <div className="grid gap-5 xl:grid-cols-[.8fr_1.2fr]">
      <Section title="Connection"><DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Roles">{roleLabel(currentSelected)}</Detail><Detail label="Interface">{titleCase(currentSelected.interface)}</Detail><Detail label="Authorization">{titleCase(currentSelected.authorization)}</Detail><Detail label="Status"><Badge variant={currentSelected.status === "ready" ? "healthy" : currentSelected.status === "reauthentication-required" ? "danger" : "warning"}>{titleCase(currentSelected.status)}</Badge></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList></Section>
      <Section title={currentSelected.interface === "browser" ? "Browser access" : "Operating scope"}>
        {currentSelected.interface === "browser" ? <div className="space-y-5"><DetailList><Detail label="Playbook version">{currentSelected.playbook_version_id ?? "Not attached"}</Detail><Detail label="Session expires">{currentSelected.authorization_expires_at ? formatDate(currentSelected.authorization_expires_at, true) : "Authentication required"}</Detail></DetailList>{currentSelected.playbook_version_id ? <Field label="Browser session store" hint="Only the metadata reference is retained here."><input className={formControl} value={sessionContainer} onChange={(event) => setSessionContainer(event.target.value)} placeholder="projects/project/secrets/platform-session" /></Field> : <div className="rounded-xl border border-[var(--border-soft)] p-5"><div className="text-[11px] font-semibold">Attach a published playbook</div><p className="mt-1 text-[10px] leading-5 text-[var(--ink-soft)]">Browser connections need a versioned procedure before authentication can begin.</p><div className="mt-4 flex gap-3"><select className={formControl} value={selectedPlaybookVersion} onChange={(event) => setSelectedPlaybookVersion(event.target.value)}><option value="">Select playbook</option>{browserPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select><Button onClick={() => { const playbook = browserPlaybooks.find((item) => item.active_version_id === selectedPlaybookVersion); if (playbook) attach.mutate({ connection: currentSelected, playbook }) }} disabled={!selectedPlaybookVersion || attach.isPending}>Attach playbook</Button></div></div>}</div> : <DetailList><Detail label="Allowed resources">{currentSelected.allowed_resources.join(", ")}</Detail><Detail label="Capabilities">{currentSelected.capabilities.length}</Detail><Detail label="Region">{currentSelected.region}</Detail><Detail label="Last validated">{currentSelected.last_validated_at ? formatDate(currentSelected.last_validated_at, true) : "Never"}</Detail></DetailList>}
      </Section>
    </div>
    {(attach.error || open.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(attach.error ?? open.error)?.message}</div>}
  </div>

  return <div className="page">
    <PageHeader eyebrow="Management" title="Connections" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add connection</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search connections or platforms" resultCount={rows.length} resultLabel="connections" onClear={() => { setSearch(""); setRole("all") }} filters={[{ label: "Role", value: role, defaultValue: "all", onChange: (event) => setRole(event.target.value), children: <><option value="all">All roles</option>{[...new Set(connections.data!.flatMap((item) => item.roles))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
    <Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Platform</TableHead><TableHead>Role</TableHead><TableHead>Interface</TableHead><TableHead>Status</TableHead><TableHead className="w-36">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => <TableRow key={connection.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={() => setSelected(connection)}><Marker icon={PlugZap} />{connection.display_name}</button></TableCell><TableCell><Provider value={connection.platform} /></TableCell><TableCell className="text-[var(--ink-soft)]">{roleLabel(connection)}</TableCell><TableCell>{titleCase(connection.interface)}</TableCell><TableCell><Badge variant={connection.status === "ready" ? "healthy" : connection.status === "reauthentication-required" ? "danger" : "warning"}>{titleCase(connection.status)}</Badge></TableCell><TableCell><Button variant="ghost" size="sm" onClick={() => setSelected(connection)}>{connection.interface === "browser" && connection.status !== "ready" ? "Set up" : "View details"} <ChevronRight className="size-3.5" /></Button></TableCell></TableRow>)}</TableBody></Table>
  </div>
}

function ConnectionSetup({ onClose, playbooks, onChanged }: { onClose: () => void; playbooks: Playbook[]; onChanged: () => Promise<void> }) {
  const [step, setStep] = useState(0)
  const [connectionRoles, setConnectionRoles] = useState<ConnectionRole[]>(["provider"])
  const [connectionInterface, setConnectionInterface] = useState<ConnectionInterface>("api")
  const [authorization, setAuthorization] = useState<ConnectionAuthorization>("oauth")
  const [name, setName] = useState("")
  const [platform, setPlatform] = useState("")
  const [region, setRegion] = useState("us-central1")
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
    if (connectionInterface === "api") setCapabilities(suggestedCapabilities(connectionRoles))
  }, [connectionInterface, connectionRoles])

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
    if (step === 1) return connectionInterface === "browser" ? Boolean(playbookVersion && sessionContainer.trim()) : Boolean(authorizationReference.trim() && (!connectionRoles.includes("provider") || (baseUrl.trim() && requestHeader.trim())))
    if (step === 2) return Boolean(resources.trim() && capabilities.trim())
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
      region,
      created_at: timestamp,
      updated_at: timestamp,
      revision: 0,
    }
    const result = await create.mutateAsync({ connection, playbook_id: connectionInterface === "browser" ? chosenPlaybook?.id : undefined, playbook_version_id: connectionInterface === "browser" ? playbookVersion : undefined })
    if (connectionInterface === "browser") setCreated(result)
    else onClose()
  }

  async function openBrowser() {
    if (!created) return
    const setup = await open.mutateAsync({ id: created.id, container: sessionContainer.trim() })
    const fragment = new URLSearchParams({ organisation_id: created.organisation_id, setup_id: setup.session.id, token: setup.token })
    window.open(`${setup.gateway_url}#${fragment}`, "_blank", "noopener,noreferrer")
  }

  if (created) return <SuccessPage eyebrow="Management / Connections" title="Connection created" onBack={onClose} actions={<><Button variant="secondary" onClick={onClose}>Finish later</Button><Button onClick={openBrowser} disabled={open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button></>}><DetailList><Detail label="Connection">{created.display_name}</Detail><Detail label="Status">Setup required</Detail><Detail label="Playbook">{chosenPlaybook?.name}</Detail><Detail label="Session store">Metadata reference saved</Detail></DetailList>{open.error && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{open.error.message}</div>}</SuccessPage>

  const primaryLabel = ["Continue to access", "Continue to scope", "Review connection", "Create connection"][step]
  return <SetupPage eyebrow="Management / Connections" title="Add connection" steps={setupSteps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={create.error?.message} primary={step < 3 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>{primaryLabel}</Button> : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Creating…" : primaryLabel}</Button>}>
    {step === 0 && <FormGrid><Field label="Connection name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production platform access" /></Field><Field label="Platform"><input className={formControl} value={platform} onChange={(event) => setPlatform(slug(event.target.value))} placeholder="platform-id" /></Field><Fieldset label="Roles" wide><div className="grid gap-2 sm:grid-cols-3">{(["provider", "runtime", "secret-store", "telemetry", "incident"] as ConnectionRole[]).map((item) => <label key={item} className="flex items-center gap-2 rounded-xl border border-[var(--border)] px-3 py-3 text-[10px] font-medium"><input type="checkbox" checked={connectionRoles.includes(item)} onChange={(event) => setConnectionRoles((current) => event.target.checked ? [...current, item] : current.filter((role) => role !== item))} /> {titleCase(item)}</label>)}</div></Fieldset><Field label="Region"><input className={formControl} value={region} onChange={(event) => setRegion(event.target.value)} /></Field></FormGrid>}
    {step === 1 && <FormGrid><Field label="Interface"><select className={formControl} value={connectionInterface} onChange={(event) => setConnectionInterface(event.target.value as ConnectionInterface)}><option value="api">API</option>{connectionRoles.includes("provider") && <option value="browser">Browser</option>}</select></Field><Field label="Authorization"><select className={formControl} value={authorization} onChange={(event) => setAuthorization(event.target.value as ConnectionAuthorization)} disabled={connectionInterface === "browser"}><option value="oauth">OAuth</option>{!connectionRoles.includes("provider") && <option value="workload-identity">Workload identity</option>}<option value="api-key">API key</option>{connectionInterface === "browser" && <option value="browser-session">Browser session</option>}</select></Field>{connectionInterface === "api" ? <><Field label="Authorization reference" hint="A secret-store or workload-identity metadata reference. FireKey never exposes its value." wide><input className={formControl} value={authorizationReference} onChange={(event) => setAuthorizationReference(event.target.value)} placeholder="projects/project/secrets/management-access" /></Field>{connectionRoles.includes("provider") && <><Field label="API base URL" wide><input className={formControl} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.platform.example" /></Field><details className="rounded-xl border border-[var(--border-soft)] sm:col-span-2"><summary className="cursor-pointer px-4 py-3 text-[10px] font-semibold">Custom HTTP operations</summary><div className="grid gap-4 border-t border-[var(--border-soft)] p-4 sm:grid-cols-2"><Field label="Request scheme"><select className={formControl} value={requestScheme} onChange={(event) => setRequestScheme(event.target.value as typeof requestScheme)} disabled={authorization === "oauth"}><option value="bearer">Bearer</option><option value="header">Custom header</option><option value="basic">HTTP Basic</option></select></Field><Field label="Request header"><input className={formControl} value={requestHeader} onChange={(event) => setRequestHeader(event.target.value)} /></Field><Field label="Header prefix"><input className={formControl} value={requestPrefix} onChange={(event) => setRequestPrefix(event.target.value)} /></Field><Field label="List path"><input className={formControl} value={listPath} onChange={(event) => setListPath(event.target.value)} /></Field><Field label="Create path"><input className={formControl} value={createPath} onChange={(event) => setCreatePath(event.target.value)} /></Field><Field label="Revoke path"><input className={formControl} value={revokePath} onChange={(event) => setRevokePath(event.target.value)} /></Field></div></details></>}</> : <><Field label="Published playbook"><select className={formControl} value={playbookVersion} onChange={(event) => setPlaybookVersion(event.target.value)}><option value="">Select playbook</option>{matchingPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select></Field><Field label="Browser session store" hint="Metadata reference for encrypted session material."><input className={formControl} value={sessionContainer} onChange={(event) => setSessionContainer(event.target.value)} placeholder="projects/project/secrets/platform-session" /></Field>{!matchingPlaybooks.length && platform && <div className="rounded-xl border border-[#eadbb8] bg-[var(--amber-soft)] p-4 text-[10px] text-[var(--amber)] sm:col-span-2">Publish a playbook for this platform before creating its browser connection.</div>}</>}</FormGrid>}
    {step === 2 && <FormGrid><Field label="Allowed resources or domains" hint="Comma-separated resource scopes this connection may operate on." wide><input className={formControl} value={resources} onChange={(event) => setResources(event.target.value)} placeholder={connectionInterface === "browser" ? "*.platform.example" : "projects/project"} /></Field><Field label="Capabilities" hint="Typed operations exposed to the FireKey agent." wide><textarea className={`${formControl} h-28 py-3`} value={capabilities} onChange={(event) => setCapabilities(event.target.value)} /></Field></FormGrid>}
    {step === 3 && <div className="grid gap-5 lg:grid-cols-2"><Section title="System"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={platform} /></Detail><Detail label="Roles">{connectionRoles.map(titleCase).join(", ")}</Detail><Detail label="Region">{region}</Detail></DetailList></Section><Section title="Access"><DetailList><Detail label="Interface">{titleCase(connectionInterface)}</Detail><Detail label="Authorization">{titleCase(authorization)}</Detail><Detail label="Playbook">{connectionInterface === "browser" ? chosenPlaybook?.name : "Not required"}</Detail><Detail label="Resources">{resources}</Detail></DetailList></Section></div>}
  </SetupPage>
}
