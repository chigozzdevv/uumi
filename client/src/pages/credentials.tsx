import { useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, ChevronRight, Plus } from "lucide-react"
import { Detail, DetailList } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { CredentialSetup } from "../components/setup"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { ManagedCredential } from "../types"
import { api } from "../lib/api"
import { formatDate, providerName, titleCase } from "../lib/format"

type CredentialTarget = "approvals" | "rotations" | "connections" | "incidents" | "applications"

export function CredentialsPage({ onNavigate, onNavigateRotation }: { onNavigate: (target: CredentialTarget) => void; onNavigateRotation: (runId: string) => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [provider, setProvider] = useState("all")
  const [selected, setSelected] = useState<ManagedCredential | null>(null)
  const [creating, setCreating] = useState(false)
  const [tab, setTab] = useState("overview")
  const [graph, runs, incidents, connections, applications, environments, policies] = useQueries({
    queries: [
      { queryKey: ["graph"], queryFn: () => api.getGraph() },
      { queryKey: ["rotations"], queryFn: () => api.getRotations() },
      { queryKey: ["incidents"], queryFn: () => api.getIncidents() },
      { queryKey: ["connections"], queryFn: () => api.getConnections() },
      { queryKey: ["applications"], queryFn: () => api.getApplications() },
      { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
      { queryKey: ["policies"], queryFn: () => api.getPolicies() },
    ],
  })
  const createCredential = useMutation({
    mutationFn: api.importCredential.bind(api),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["graph"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ])
    },
  })

  const rows = useMemo(() => {
    const credentials = graph.data?.credentials ?? []
    const term = search.trim().toLowerCase()
    return credentials.filter((item) => (provider === "all" || item.provider === provider) && (!term || `${item.display_name} ${item.id} ${item.provider}`.toLowerCase().includes(term)))
  }, [graph.data, provider, search])

  const queries = [graph, runs, incidents, connections, applications, environments, policies]
  if (queries.some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = queries.find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  function operationalState(item: ManagedCredential) {
    const run = runs.data!.find((entry) => entry.credential_id === item.id && !["completed", "compensated"].includes(entry.status))
    const incident = incidents.data!.find((entry) => entry.credential_id === item.id && !["resolved", "dismissed"].includes(entry.status))
    const connection = connections.data!.find((entry) => entry.id === item.connection_id)
    if (run?.status === "running" || run?.status === "recovering") return { label: "Rotating", variant: "active" as const }
    if (run || incident || connection?.status !== "ready") return { label: "Pending", variant: "warning" as const }
    return { label: "Active", variant: "healthy" as const }
  }

  function actionFor(item: ManagedCredential): { label: string; target?: CredentialTarget; runId?: string } {
    const run = runs.data!.find((entry) => entry.credential_id === item.id && !["completed", "compensated"].includes(entry.status))
    const incident = incidents.data!.find((entry) => entry.credential_id === item.id && !["resolved", "dismissed"].includes(entry.status))
    const connection = connections.data!.find((entry) => entry.id === item.connection_id)
    if (run?.status === "paused" && run.stage === "approval") return { label: "Review approval", target: "approvals" }
    if (connection?.status === "reauthentication-required" || connection?.status === "degraded") return { label: "Open connection", target: "connections" }
    if (run) return { label: "Open rotation", target: "rotations", runId: run.id }
    if (incident) return { label: "Open incident", target: "incidents" }
    return { label: "View details" }
  }

  function performAction(item: ManagedCredential) {
    const action = actionFor(item)
    if (action.runId) onNavigateRotation(action.runId)
    else if (action.target) onNavigate(action.target)
    else { setSelected(item); setTab("overview") }
  }

  const selectedServices = selected ? graph.data!.services.filter((service) => selected.consumer_ids.includes(service.id)) : []
  const selectedConnection = selected ? connections.data!.find((item) => item.id === selected.connection_id) : undefined
  const selectedSecretStore = selected ? connections.data!.find((item) => item.id === selected.secret_store_connection_id) : undefined
  const selectedAction = selected ? actionFor(selected) : undefined

  if (creating) return <CredentialSetup isOpen onClose={() => setCreating(false)} graph={graph.data!} connections={connections.data!} applications={applications.data!} environments={environments.data!} policies={policies.data!} onCreate={(input) => createCredential.mutateAsync(input)} />

  if (selected) return (
    <div className="page">
      <PageHeader eyebrow="Inventory / Credentials" title={selected.display_name} titlePrefix={<Provider value={selected.provider} label={false} />} onBack={() => setSelected(null)} actions={<>{(selectedAction?.target || selectedAction?.runId) && <Button onClick={() => performAction(selected)}>{selectedAction.label}<ArrowUpRight className="size-3.5" /></Button>}<Button variant="secondary" onClick={() => onNavigate("applications")}>View applications</Button></>} />
      <div className="mb-6 flex gap-1">{["overview", "consumers", "control"].map((item) => <button key={item} className={`focus-ring border-b-2 px-4 py-3 text-[11px] font-semibold capitalize ${tab === item ? "border-[var(--ink)] text-[var(--ink)]" : "border-transparent text-[var(--ink-muted)] hover:text-[var(--ink)]"}`} onClick={() => setTab(item)}>{item}</button>)}</div>
      <section className="rounded-2xl border border-[var(--border)] bg-white p-6">
        {tab === "overview" && <DetailList><Detail label="Type">{titleCase(selected.kind)}</Detail><Detail label="Scopes">{selected.scopes.join(", ") || "None"}</Detail><Detail label="Provider ID">{selected.provider_id ?? "Not recorded"}</Detail><Detail label="Consumers">{selected.consumer_ids.length}</Detail><Detail label="Secret reference"><span className="mono-code break-all">{selected.secret_reference}</span></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList>}
        {tab === "consumers" && <div className="divide-y divide-[var(--border-soft)]">{selectedServices.map((service) => <div key={service.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_1.5fr]"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="text-[10px] text-[var(--ink-soft)]"><div>{service.runtime_resource}</div><div className="mt-1 text-[var(--ink-muted)]">{applications.data!.find((item) => item.id === service.application_id)?.display_name} · {environments.data!.find((item) => item.id === service.environment_id)?.display_name}</div></div></div>)}</div>}
        {tab === "control" && <DetailList><Detail label="Management connection">{selectedConnection?.display_name}</Detail><Detail label="Interface">{titleCase(selectedConnection?.interface ?? "unknown")}</Detail><Detail label="Secret store">{selectedSecretStore?.display_name}</Detail><Detail label="Policy">{policies.data!.find((item) => item.active_version_id === selected.policy_version)?.name}</Detail><Detail label="Browser Playbook">{selectedConnection?.interface === "browser" ? selectedConnection.playbook_version_id : "Not required"}</Detail><Detail label="Connection status"><Badge variant={selectedConnection?.status === "ready" ? "healthy" : "danger"}>{titleCase(selectedConnection?.status ?? "unknown")}</Badge></Detail></DetailList>}
      </section>
    </div>
  )

  return (
    <div className="page">
      <PageHeader
        eyebrow="Inventory"
        title="Credentials"
        actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add credential</Button>}
      />

      <Toolbar
        value={search}
        onChange={setSearch}
        placeholder="Search credentials or providers"
        onClear={() => { setSearch(""); setProvider("all") }}
        filters={[{
          label: "Provider",
          defaultValue: "all",
          value: provider,
          onChange: (event) => setProvider(event.target.value),
          children: <><option value="all">All providers</option>{[...new Set(graph.data!.credentials.map((item) => item.provider))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</>,
        }]}
      />

      <div>
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Consumer</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead className="text-right">Action</TableHead></TableRow></TableHeader>
          <TableBody>
            {rows.map((item) => {
              const status = operationalState(item)
              const service = graph.data!.services.find((entry) => entry.id === item.consumer_ids[0])
              return (
                <TableRow key={item.id}>
                  <TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left" onClick={() => { setSelected(item); setTab("overview") }}><Provider value={item.provider} label={false} /><div><div className="font-medium hover:underline">{item.display_name}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{titleCase(item.kind)}</div></div></button></TableCell>
                  <TableCell><div>{service?.display_name ?? "Unmapped"}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{item.consumer_ids.length} binding{item.consumer_ids.length === 1 ? "" : "s"}</div></TableCell>
                  <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
                  <TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(item.updated_at)}</TableCell>
                  <TableCell className="text-right"><Button variant="ghost" size="sm" onClick={() => performAction(item)}>{actionFor(item).label}<ChevronRight className="size-3.5" /></Button></TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

    </div>
  )
}
