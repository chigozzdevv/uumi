import { useMemo, useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { ArrowRight, FileInput, Plus, Settings2 } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Drawer } from "../components/ui/drawer"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { ManagedCredential } from "../types"
import { api } from "../lib/api"
import { formatDate, providerName, shortId, titleCase } from "../lib/format"

export function CredentialsPage() {
  const [search, setSearch] = useState("")
  const [provider, setProvider] = useState("all")
  const [selected, setSelected] = useState<ManagedCredential | null>(null)
  const [creating, setCreating] = useState(false)
  const [tab, setTab] = useState("overview")
  const [graph, runs, incidents, connections] = useQueries({
    queries: [
      { queryKey: ["graph"], queryFn: () => api.getGraph() },
      { queryKey: ["rotations"], queryFn: () => api.getRotations() },
      { queryKey: ["incidents"], queryFn: () => api.getIncidents() },
      { queryKey: ["connections"], queryFn: () => api.getConnections() },
    ],
  })

  const rows = useMemo(() => {
    const credentials = graph.data?.credentials ?? []
    const term = search.trim().toLowerCase()
    return credentials.filter((item) => (provider === "all" || item.provider === provider) && (!term || `${item.display_name} ${item.id} ${item.provider}`.toLowerCase().includes(term)))
  }, [graph.data, provider, search])

  if ([graph, runs, incidents, connections].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [graph, runs, incidents, connections].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  function operationalState(item: ManagedCredential) {
    const run = runs.data!.find((entry) => entry.credential_id === item.id && !["completed", "compensated"].includes(entry.status))
    const incident = incidents.data!.find((entry) => entry.credential_id === item.id && !["resolved", "dismissed"].includes(entry.status))
    const connection = connections.data!.find((entry) => entry.id === item.connection_id)
    if (run?.status === "failed" || connection?.status === "reauthentication-required") return { label: "Action required", variant: "danger" as const }
    if (incident) return { label: "Exposed", variant: "danger" as const }
    if (run) return { label: titleCase(run.status), variant: "active" as const }
    return { label: "Healthy", variant: "healthy" as const }
  }

  const selectedServices = selected ? graph.data!.services.filter((service) => selected.consumer_ids.includes(service.id)) : []
  const selectedConnection = selected ? connections.data!.find((item) => item.id === selected.connection_id) : undefined

  return (
    <div className="page">
      <PageHeader
        section="Inventory · Credentials"
        title="Credentials"
        description="Metadata-only inventory of managed credential identities, active generations, and the services that consume them."
        actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add credential</Button>}
      />

      <Toolbar
        value={search}
        onChange={setSearch}
        placeholder="Search credentials by name, provider, or ID"
        filters={[{
          label: "Provider",
          value: provider,
          onChange: (event) => setProvider(event.target.value),
          children: <><option value="all">All providers</option>{[...new Set(graph.data!.credentials.map((item) => item.provider))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</>,
        }]}
      />

      <div className="panel overflow-hidden">
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Provider</TableHead><TableHead>Consumer</TableHead><TableHead>Generation</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader>
          <TableBody>
            {rows.map((item) => {
              const status = operationalState(item)
              const service = graph.data!.services.find((entry) => entry.id === item.consumer_ids[0])
              return (
                <TableRow key={item.id} className="cursor-pointer" onClick={() => { setSelected(item); setTab("overview") }}>
                  <TableCell><div className="font-semibold">{item.display_name}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{item.kind}</div></TableCell>
                  <TableCell><Provider value={item.provider} /></TableCell>
                  <TableCell><div>{service?.display_name ?? "Unmapped"}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{item.consumer_ids.length} binding{item.consumer_ids.length === 1 ? "" : "s"}</div></TableCell>
                  <TableCell className="mono text-[10px] text-[var(--ink-soft)]">{shortId(item.active_generation_id)}</TableCell>
                  <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
                  <TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(item.updated_at)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
      <div className="mt-3 text-[10px] text-[var(--ink-muted)]">Showing {rows.length} of {graph.data!.credentials.length} managed credentials</div>

      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Credential"} subtitle={selected?.id}>
        {selected && (
          <>
            <div className="mb-6 flex gap-1 border-b border-[var(--border)]">
              {["overview", "consumers", "control"].map((item) => <button key={item} className={`focus-ring -mb-px border-b-2 px-3 pb-3 text-[10px] font-semibold capitalize ${tab === item ? "border-[var(--accent)] text-[var(--accent)]" : "border-transparent text-[var(--ink-muted)]"}`} onClick={() => setTab(item)}>{item}</button>)}
            </div>
            {tab === "overview" && <><Section title="Identity"><DetailList><Detail label="Provider"><Provider value={selected.provider} /></Detail><Detail label="Credential type">{titleCase(selected.kind)}</Detail><Detail label="Provider ID"><span className="mono text-[10px]">{selected.provider_id}</span></Detail><Detail label="Active generation"><span className="mono text-[10px]">{selected.active_generation_id}</span></Detail><Detail label="Scopes">{selected.scopes.join(", ")}</Detail></DetailList></Section><Section title="Operational state"><DetailList><Detail label="Status"><Badge variant={operationalState(selected).variant}>{operationalState(selected).label}</Badge></Detail><Detail label="Last changed">{formatDate(selected.updated_at, true)}</Detail><Detail label="Revision">{selected.revision}</Detail></DetailList></Section></>}
            {tab === "consumers" && <Section title="Bound services"><div className="space-y-2">{selectedServices.map((service) => <div key={service.id} className="rounded-xl border border-[var(--border-soft)] bg-white/60 p-4"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{service.runtime_resource}</div><div className="mt-3 text-[10px] text-[var(--ink-soft)]">{service.identity}</div></div>)}</div></Section>}
            {tab === "control" && <><Section title="Control bindings"><DetailList><Detail label="Policy"><span className="mono text-[10px]">{selected.policy_version}</span></Detail><Detail label="Playbook"><span className="mono text-[10px]">{selected.playbook_version}</span></Detail><Detail label="Connection">{selectedConnection?.display_name}</Detail><Detail label="Auth status"><Badge variant={selectedConnection?.status === "ready" ? "healthy" : "danger"}>{titleCase(selectedConnection?.status ?? "unknown")}</Badge></Detail></DetailList></Section><p className="text-[10px] leading-5 text-[var(--ink-soft)]">FireKey stores only operational metadata here. Secret values remain behind the Auth Broker and declared secret-store boundary.</p></>}
          </>
        )}
      </Drawer>

      <Drawer isOpen={creating} onClose={() => setCreating(false)} title="Add credential" subtitle="Choose the inventory path">
        <div className="space-y-3">
          <button className="focus-ring flex w-full items-center gap-4 rounded-2xl border border-[var(--border)] bg-white p-5 text-left hover:border-[#bdb9cf]" onClick={() => setCreating(false)}><span className="grid size-10 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><FileInput className="size-4" /></span><span className="flex-1"><span className="block text-[12px] font-semibold">Import from connected provider</span><span className="mt-1 block text-[10px] leading-4 text-[var(--ink-soft)]">Select provider metadata, then bind the current generation to its consumers.</span></span><ArrowRight className="size-4 text-[var(--ink-muted)]" /></button>
          <button className="focus-ring flex w-full items-center gap-4 rounded-2xl border border-[var(--border)] bg-white p-5 text-left hover:border-[#bdb9cf]" onClick={() => setCreating(false)}><span className="grid size-10 place-items-center rounded-xl bg-[#ececea] text-[var(--ink-soft)]"><Settings2 className="size-4" /></span><span className="flex-1"><span className="block text-[12px] font-semibold">Configure manually</span><span className="mt-1 block text-[10px] leading-4 text-[var(--ink-soft)]">Map a provider, secret reference, consumers, policy, and approved playbook.</span></span><ArrowRight className="size-4 text-[var(--ink-muted)]" /></button>
        </div>
      </Drawer>
    </div>
  )
}
