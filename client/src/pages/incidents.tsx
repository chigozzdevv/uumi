import { useMemo, useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { ArrowUpRight, GitBranch, ShieldAlert } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Drawer } from "../components/ui/drawer"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Incident } from "../types"
import { api } from "../lib/api"
import { formatDate, shortId, titleCase } from "../lib/format"

function severityVariant(severity: Incident["severity"]) {
  if (severity === "critical" || severity === "high") return "danger" as const
  if (severity === "medium") return "warning" as const
  return "neutral" as const
}

export function IncidentsPage({ onNavigateRotation }: { onNavigateRotation: (runId: string) => void }) {
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("open")
  const [selected, setSelected] = useState<Incident | null>(null)
  const [incidents, graph] = useQueries({ queries: [
    { queryKey: ["incidents"], queryFn: () => api.getIncidents() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (incidents.data ?? []).filter((item) => {
      const open = !["resolved", "dismissed"].includes(item.status)
      const matchesStatus = status === "all" || (status === "open" ? open : item.status === status)
      const haystack = `${item.source} ${item.source_event_id} ${item.resource.repository} ${item.resource.service}`.toLowerCase()
      return matchesStatus && (!term || haystack.includes(term))
    })
  }, [incidents.data, search, status])

  if ([incidents, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [incidents, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const credentialName = (id: string | null) => graph.data!.credentials.find((item) => item.id === id)?.display_name ?? "Unconfirmed"

  return (
    <div className="page">
      <PageHeader section="Operations · Incidents" title="Incidents" description="Authenticated and correlated security signals, ordered by severity and evidence confidence." />
      <Toolbar value={search} onChange={setSearch} placeholder="Search incidents, repositories, or services" filters={[{ label: "Status", value: status, onChange: (event) => setStatus(event.target.value), children: <><option value="open">Open incidents</option><option value="all">All incidents</option><option value="action-required">Action required</option><option value="rotation-started">Rotation started</option><option value="resolved">Resolved</option></> }]} />

      <div className="panel overflow-hidden">
        <Table>
          <TableHeader><TableRow><TableHead>Severity</TableHead><TableHead>Signal</TableHead><TableHead>Correlated credential</TableHead><TableHead>Confidence</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Detected</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map((incident) => (
            <TableRow key={incident.id} className="cursor-pointer" onClick={() => setSelected(incident)}>
              <TableCell><Badge variant={severityVariant(incident.severity)}>{incident.severity}</Badge></TableCell>
              <TableCell><div className="font-semibold">{titleCase(incident.source)}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{incident.source_event_id}</div></TableCell>
              <TableCell><div>{credentialName(incident.credential_id)}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{incident.resource.service ?? incident.resource.repository}</div></TableCell>
              <TableCell><Badge variant={incident.confidence === "verified" ? "healthy" : "active"}>{incident.confidence}</Badge></TableCell>
              <TableCell><Badge variant={incident.status === "resolved" ? "healthy" : incident.status === "action-required" ? "warning" : "active"}>{titleCase(incident.status)}</Badge></TableCell>
              <TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(incident.created_at, true)}</TableCell>
            </TableRow>
          ))}</TableBody>
        </Table>
      </div>

      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? titleCase(selected.source) : "Incident"} subtitle={selected?.id}>
        {selected && <>
          <div className="mb-6 flex flex-wrap items-center gap-2"><Badge variant={severityVariant(selected.severity)}>{selected.severity}</Badge><Badge variant={selected.confidence === "verified" ? "healthy" : "active"}>{selected.confidence} confidence</Badge><Badge variant={selected.status === "action-required" ? "warning" : selected.status === "resolved" ? "healthy" : "active"}>{titleCase(selected.status)}</Badge></div>
          <Section title="Source"><DetailList><Detail label="Event ID"><span className="mono text-[10px]">{selected.source_event_id}</span></Detail><Detail label="Repository">{selected.resource.repository ?? "—"}</Detail><Detail label="Project">{selected.resource.project ?? "—"}</Detail><Detail label="Service">{selected.resource.service ?? "—"}</Detail><Detail label="Observed">{formatDate(selected.created_at, true)}</Detail></DetailList></Section>
          <Section title="Correlation"><div className="rounded-2xl border border-[var(--border)] bg-white/65 p-5"><div className="flex items-start gap-3"><span className="grid size-8 shrink-0 place-items-center rounded-xl bg-[var(--red-soft)] text-[var(--red)]"><ShieldAlert className="size-4" /></span><div><div className="text-[12px] font-semibold">{credentialName(selected.credential_id)}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{selected.credential_id}</div></div></div><div className="mt-4 space-y-2">{selected.candidates[0]?.reasons.map((reason) => <div key={reason} className="flex items-center gap-2 text-[10px] text-[var(--ink-soft)]"><GitBranch className="size-3 text-[var(--accent)]" /> {reason}</div>)}</div></div></Section>
          {selected.run_id && <Section title="Containment"><div className="rounded-xl border border-[var(--border-soft)] bg-white/65 p-4"><div className="data-label">Linked rotation</div><div className="mono mt-2 text-[10px]">{shortId(selected.run_id, 30)}</div><Button className="mt-4 w-full" onClick={() => onNavigateRotation(selected.run_id!)}>Open live run <ArrowUpRight className="size-3.5" /></Button></div></Section>}
        </>}
      </Drawer>
    </div>
  )
}
