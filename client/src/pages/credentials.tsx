import { useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Plus } from "lucide-react"
import { Detail, DetailList } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { CredentialSetup } from "../components/setup"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { ManagedCredential } from "../types"
import { api } from "../lib/api"
import { formatDate, providerName, titleCase } from "../lib/format"

type CredentialTarget = "approvals" | "rotations" | "connections" | "incidents" | "playbooks"

export function CredentialsPage({ onNavigate, onNavigateRotation }: { onNavigate: (target: CredentialTarget) => void; onNavigateRotation: (runId: string) => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [provider, setProvider] = useState("all")
  const [selected, setSelected] = useState<ManagedCredential | null>(null)
  const [creating, setCreating] = useState(false)
  const [tab, setTab] = useState("overview")
  const [graph, runs, incidents, connections, applications, environments, policies, playbooks] = useQueries({
    queries: [
      { queryKey: ["graph"], queryFn: () => api.getGraph() },
      { queryKey: ["rotations"], queryFn: () => api.getRotations() },
      { queryKey: ["incidents"], queryFn: () => api.getIncidents() },
      { queryKey: ["connections"], queryFn: () => api.getConnections() },
      { queryKey: ["applications"], queryFn: () => api.getApplications() },
      { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
      { queryKey: ["policies"], queryFn: () => api.getPolicies() },
      { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
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

  const queries = [graph, runs, incidents, connections, applications, environments, policies, playbooks]
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

  function actionFor(item: ManagedCredential): { label: string; target: CredentialTarget; runId?: string } {
    const run = runs.data!.find((entry) => entry.credential_id === item.id && !["completed", "compensated"].includes(entry.status))
    const incident = incidents.data!.find((entry) => entry.credential_id === item.id && !["resolved", "dismissed"].includes(entry.status))
    const connection = connections.data!.find((entry) => entry.id === item.connection_id)
    if (run?.status === "paused" && run.stage === "approval") return { label: "Review approval", target: "approvals" }
    if (connection?.status === "reauthentication-required" || connection?.status === "degraded") return { label: "Open connection", target: "connections" }
    if (run) return { label: "Open rotation", target: "rotations", runId: run.id }
    if (incident) return { label: "Open incident", target: "incidents" }
    return { label: "Open playbook", target: "playbooks" }
  }

  const selectedServices = selected ? graph.data!.services.filter((service) => selected.consumer_ids.includes(service.id)) : []
  const selectedConnection = selected ? connections.data!.find((item) => item.id === selected.connection_id) : undefined
  const selectedState = selected ? operationalState(selected) : undefined
  const selectedAction = selected ? actionFor(selected) : undefined

  return (
    <div className="page">
      <PageHeader
        section="Inventory · Credentials"
        actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add credential</Button>}
      />

      <Toolbar
        value={search}
        onChange={setSearch}
        placeholder="Search credentials or providers"
        filters={[{
          label: "Provider",
          value: provider,
          onChange: (event) => setProvider(event.target.value),
          children: <><option value="all">All providers</option>{[...new Set(graph.data!.credentials.map((item) => item.provider))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</>,
        }]}
      />

      <div>
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Consumer</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader>
          <TableBody>
            {rows.map((item) => {
              const status = operationalState(item)
              const service = graph.data!.services.find((entry) => entry.id === item.consumer_ids[0])
              return (
                <TableRow key={item.id} className="cursor-pointer" onClick={() => { setSelected(item); setTab("overview") }}>
                  <TableCell><div className="flex items-center gap-3"><Provider value={item.provider} label={false} /><div><div className="font-medium">{item.display_name}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{titleCase(item.kind)}</div></div></div></TableCell>
                  <TableCell><div>{service?.display_name ?? "Unmapped"}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{item.consumer_ids.length} binding{item.consumer_ids.length === 1 ? "" : "s"}</div></TableCell>
                  <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
                  <TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(item.updated_at)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <Modal
        isOpen={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.display_name ?? "Credential"}
        actions={selected && <Button onClick={() => { setSelected(null); if (selectedAction!.runId) onNavigateRotation(selectedAction!.runId); else onNavigate(selectedAction!.target) }}>{selectedAction!.label}<ArrowUpRight className="size-3.5" /></Button>}
      >
        {selected && (
          <>
            <div className="mb-5 flex items-center justify-between gap-4 rounded-xl bg-white/65 px-4 py-3.5">
              <div className="min-w-0">
                <Provider value={selected.provider} />
                <div className="mt-1 text-[9px] text-[var(--ink-muted)]">{titleCase(selected.kind)}</div>
              </div>
              <Badge variant={selectedState!.variant}>{selectedState!.label}</Badge>
            </div>
            <div className="mb-6 flex gap-5">
              {["overview", "consumers", "control"].map((item) => <button key={item} className={`focus-ring -mb-px border-b-2 px-3 pb-3 text-[10px] font-semibold capitalize ${tab === item ? "border-[var(--accent)] text-[var(--accent)]" : "border-transparent text-[var(--ink-muted)]"}`} onClick={() => setTab(item)}>{item}</button>)}
            </div>
            {tab === "overview" && <DetailList><Detail label="Type">{titleCase(selected.kind)}</Detail><Detail label="Scopes">{selected.scopes.join(", ") || "None"}</Detail><Detail label="Consumers">{selected.consumer_ids.length}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList>}
            {tab === "consumers" && <div className="grid gap-5 sm:grid-cols-2">{selectedServices.map((service) => <div key={service.id}><div className="text-[11px] font-semibold">{service.display_name}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{service.runtime_resource}</div></div>)}</div>}
            {tab === "control" && <DetailList><Detail label="Connection">{selectedConnection?.display_name}</Detail><Detail label="Status"><Badge variant={selectedConnection?.status === "ready" ? "healthy" : "danger"}>{titleCase(selectedConnection?.status ?? "unknown")}</Badge></Detail></DetailList>}
          </>
        )}
      </Modal>

      <CredentialSetup
        isOpen={creating}
        onClose={() => setCreating(false)}
        graph={graph.data!}
        connections={connections.data!}
        applications={applications.data!}
        environments={environments.data!}
        policies={policies.data!}
        playbooks={playbooks.data!}
        onCreate={(input) => createCredential.mutateAsync(input)}
      />
    </div>
  )
}
