import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, ArrowRight, ExternalLink, PlugZap, Plus } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Journey } from "../components/journey"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Connection, ConnectionAuthorization, ConnectionInterface, ConnectionRole, Playbook } from "../types"
import { api, type CreateConnectionInput } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"
const setupSteps = ["System", "Access", "Scope", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

function Label({ title, children }: { title: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[10px] font-medium text-[var(--ink-soft)]">{title}</span>{children}</label>
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

  return (
    <div className="page">
      <PageHeader section="System · Connections" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add connection</Button>} />
      <Toolbar value={search} onChange={setSearch} placeholder="Search connections or platforms" filters={[{ label: "Role", value: role, onChange: (event) => setRole(event.target.value), children: <><option value="all">All roles</option>{[...new Set(connections.data!.flatMap((item) => item.roles))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Platform</TableHead><TableHead>Role</TableHead><TableHead>Interface</TableHead><TableHead>Status</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => <TableRow key={connection.id} className="cursor-pointer" onClick={() => setSelected(connection)}><TableCell><div className="flex items-center gap-3"><Marker icon={PlugZap} tone="blue" /><span className="font-medium">{connection.display_name}</span></div></TableCell><TableCell><Provider value={connection.platform} /></TableCell><TableCell className="text-[var(--ink-soft)]">{roleLabel(connection)}</TableCell><TableCell>{titleCase(connection.interface)}</TableCell><TableCell><Badge variant={connection.status === "ready" ? "healthy" : connection.status === "reauthentication-required" ? "danger" : "warning"}>{titleCase(connection.status)}</Badge></TableCell></TableRow>)}</TableBody></Table></div>
      <Modal
        isOpen={Boolean(currentSelected)}
        onClose={() => setSelected(null)}
        title={currentSelected?.display_name ?? "Connection"}
        actions={currentSelected?.interface === "browser" ? currentSelected.playbook_version_id
          ? <Button onClick={() => openBrowser(currentSelected)} disabled={!sessionContainer.trim() || open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button>
          : <Button onClick={() => { const playbook = browserPlaybooks.find((item) => item.active_version_id === selectedPlaybookVersion); if (playbook) attach.mutate({ connection: currentSelected, playbook }) }} disabled={!selectedPlaybookVersion || attach.isPending}>Attach Playbook</Button>
          : undefined}
      >
        {currentSelected && <><Section title="Connection"><DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Role">{roleLabel(currentSelected)}</Detail><Detail label="Interface">{titleCase(currentSelected.interface)}</Detail><Detail label="Authorization">{titleCase(currentSelected.authorization)}</Detail><Detail label="Status"><Badge variant={currentSelected.status === "ready" ? "healthy" : "danger"}>{titleCase(currentSelected.status)}</Badge></Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList></Section>{currentSelected.interface === "browser" && <Section title="Browser procedure"><div className="space-y-4"><DetailList><Detail label="Playbook version">{currentSelected.playbook_version_id ?? "Not attached"}</Detail><Detail label="Session expiry">{currentSelected.authorization_expires_at ? formatDate(currentSelected.authorization_expires_at, true) : "Not authenticated"}</Detail></DetailList>{currentSelected.playbook_version_id ? <Label title="Browser session store"><input className={field} value={sessionContainer} onChange={(event) => setSessionContainer(event.target.value)} placeholder="projects/acme-prod/secrets/platform-session" /></Label> : <Label title="Published Playbook"><select className={field} value={selectedPlaybookVersion} onChange={(event) => setSelectedPlaybookVersion(event.target.value)}><option value="">Select Playbook</option>{browserPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select></Label>}</div></Section>}{(attach.error || open.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(attach.error ?? open.error)?.message}</div>}</>}
      </Modal>
      <ConnectionSetup isOpen={creating} onClose={() => setCreating(false)} playbooks={playbooks.data!} onChanged={async () => { await queryClient.invalidateQueries({ queryKey: ["connections"] }) }} />
    </div>
  )
}

function ConnectionSetup({ isOpen, onClose, playbooks, onChanged }: { isOpen: boolean; onClose: () => void; playbooks: Playbook[]; onChanged: () => Promise<void> }) {
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
    if (!isOpen) return
    setStep(0); setConnectionRoles(["provider"]); setConnectionInterface("api"); setAuthorization("oauth"); setName(""); setPlatform(""); setRegion("us-central1"); setAuthorizationReference(""); setResources(""); setCapabilities(""); setBaseUrl(""); setRequestScheme("bearer"); setRequestHeader("Authorization"); setRequestPrefix("Bearer "); setListPath("/credentials"); setCreatePath("/credentials"); setRevokePath("/credentials/{provider_id}"); setPlaybookVersion(""); setSessionContainer(""); setCreated(null); create.reset(); open.reset()
  }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps

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

  return <Modal isOpen={isOpen} onClose={onClose} title="Add connection" size="wide" footerStart={!created && step > 0 ? <Button variant="ghost" onClick={() => setStep((value) => value - 1)}><ArrowLeft className="size-3.5" /> Back</Button> : undefined} actions={created ? <><Button variant="ghost" onClick={onClose}>Done</Button><Button onClick={openBrowser} disabled={open.isPending}>Open browser <ExternalLink className="size-3.5" /></Button></> : step < setupSteps.length - 1 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>Continue <ArrowRight className="size-3.5" /></Button> : <Button onClick={submit} disabled={create.isPending}>{create.isPending ? "Creating…" : "Create connection"}</Button>}>
    {!created && <Journey steps={setupSteps} current={step} />}
    {created && <div className="rounded-2xl border border-[var(--border)] bg-white p-6"><div className="text-[13px] font-semibold">Connection created</div><p className="mt-2 text-[10px] leading-5 text-[var(--ink-soft)]">The published Playbook is attached. Open the isolated browser to establish the provider session.</p></div>}
    {!created && step === 0 && <div className="grid gap-4 sm:grid-cols-2"><Label title="Connection name"><input className={field} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production platform access" /></Label><Label title="Platform"><input className={field} value={platform} onChange={(event) => setPlatform(slug(event.target.value))} placeholder="platform-id" /></Label><div className="sm:col-span-2"><div className="mb-1.5 text-[10px] font-medium text-[var(--ink-soft)]">Roles</div><div className="grid gap-2 sm:grid-cols-3">{(["provider", "runtime", "secret-store", "telemetry", "incident"] as ConnectionRole[]).map((item) => <label key={item} className="flex items-center gap-2 rounded-xl border border-[var(--border)] bg-white px-3 py-3 text-[10px] font-medium"><input type="checkbox" checked={connectionRoles.includes(item)} onChange={(event) => setConnectionRoles((current) => event.target.checked ? [...current, item] : current.filter((role) => role !== item))} /> {titleCase(item)}</label>)}</div></div><Label title="Region"><input className={field} value={region} onChange={(event) => setRegion(event.target.value)} /></Label></div>}
    {!created && step === 1 && <div className="grid gap-4 sm:grid-cols-2"><Label title="Interface"><select className={field} value={connectionInterface} onChange={(event) => setConnectionInterface(event.target.value as ConnectionInterface)}><option value="api">API</option>{connectionRoles.includes("provider") && <option value="browser">Browser</option>}</select></Label><Label title="Authorization"><select className={field} value={authorization} onChange={(event) => setAuthorization(event.target.value as ConnectionAuthorization)} disabled={connectionInterface === "browser"}><option value="oauth">OAuth</option>{!connectionRoles.includes("provider") && <option value="workload-identity">Workload identity</option>}<option value="api-key">API key</option>{connectionInterface === "browser" && <option value="browser-session">Browser session</option>}</select></Label>{connectionInterface === "api" ? <><div className="sm:col-span-2"><Label title="Authorization reference"><input className={field} value={authorizationReference} onChange={(event) => setAuthorizationReference(event.target.value)} placeholder="Secret Manager or workload identity reference" /></Label></div>{connectionRoles.includes("provider") && <><div className="sm:col-span-2"><Label title="API base URL"><input className={field} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.platform.example" /></Label></div><Label title="Request scheme"><select className={field} value={requestScheme} onChange={(event) => setRequestScheme(event.target.value as typeof requestScheme)} disabled={authorization === "oauth"}><option value="bearer">Bearer</option><option value="header">Custom header</option><option value="basic">HTTP Basic</option></select></Label><Label title="Request header"><input className={field} value={requestHeader} onChange={(event) => setRequestHeader(event.target.value)} placeholder="X-API-Key" /></Label><div className="sm:col-span-2"><Label title="Header prefix (optional)"><input className={field} value={requestPrefix} onChange={(event) => setRequestPrefix(event.target.value)} placeholder="Bearer " /></Label></div><Label title="List path"><input className={field} value={listPath} onChange={(event) => setListPath(event.target.value)} /></Label><Label title="Create path"><input className={field} value={createPath} onChange={(event) => setCreatePath(event.target.value)} /></Label><div className="sm:col-span-2"><Label title="Revoke path"><input className={field} value={revokePath} onChange={(event) => setRevokePath(event.target.value)} /></Label></div></>}</> : <><Label title="Published Playbook"><select className={field} value={playbookVersion} onChange={(event) => setPlaybookVersion(event.target.value)}><option value="">Select Playbook</option>{matchingPlaybooks.map((item) => <option key={item.id} value={item.active_version_id!}>{item.name}</option>)}</select></Label><Label title="Browser session store"><input className={field} value={sessionContainer} onChange={(event) => setSessionContainer(event.target.value)} placeholder="projects/acme-prod/secrets/platform-session" /></Label>{!matchingPlaybooks.length && platform && <div className="sm:col-span-2 rounded-xl bg-[var(--amber-soft)] p-4 text-[10px] text-[var(--amber)]">Publish a Playbook for this platform before creating its browser connection.</div>}</>}</div>}
    {!created && step === 2 && <div className="grid gap-4 sm:grid-cols-2"><div className="sm:col-span-2"><Label title="Allowed resources or domains"><input className={field} value={resources} onChange={(event) => setResources(event.target.value)} placeholder={connectionInterface === "browser" ? "*.platform.example" : "projects/acme-prod"} /></Label></div><div className="sm:col-span-2"><Label title="Capabilities"><input className={field} value={capabilities} onChange={(event) => setCapabilities(event.target.value)} placeholder="runtime.deployCandidate, runtime.rollback" /></Label></div></div>}
    {!created && step === 3 && <><Section title="System"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={platform} /></Detail><Detail label="Roles">{connectionRoles.map(titleCase).join(", ")}</Detail><Detail label="Region">{region}</Detail></DetailList></Section><Section title="Access"><DetailList><Detail label="Interface">{titleCase(connectionInterface)}</Detail><Detail label="Authorization">{titleCase(authorization)}</Detail><Detail label="Playbook">{connectionInterface === "browser" ? chosenPlaybook?.name : "Not required"}</Detail><Detail label="Resources">{resources}</Detail></DetailList></Section></>}
    {(create.error || open.error) && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{(create.error ?? open.error)?.message}</div>}
  </Modal>
}
