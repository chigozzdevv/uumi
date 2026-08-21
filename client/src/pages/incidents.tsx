import { useMemo, useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { ArrowUpRight, ChevronRight } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Incident } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

function severityVariant(severity: Incident["severity"]) {
  if (severity === "critical" || severity === "high") return "danger" as const
  if (severity === "medium") return "warning" as const
  return "neutral" as const
}

export function IncidentsPage({ onNavigateRotation }: { onNavigateRotation: (runId: string) => void }) {
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("open")
  const [selected, setSelected] = useState<Incident | null>(null)
  const [detailTab, setDetailTab] = useState<"source" | "correlation">("source")
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

  if (selected) return <div className="page">
    <PageHeader eyebrow="Operations / Incidents" title={titleCase(selected.source)} onBack={() => setSelected(null)} actions={selected.run_id ? <Button onClick={() => onNavigateRotation(selected.run_id!)}>Open rotation <ArrowUpRight className="size-3.5" /></Button> : undefined} />
    <div className="mb-5 flex flex-wrap items-center gap-2"><Badge variant={severityVariant(selected.severity)}>{selected.severity}</Badge><Badge variant={selected.confidence === "verified" ? "healthy" : "active"}>{selected.confidence} confidence</Badge><Badge variant={selected.status === "action-required" ? "warning" : selected.status === "resolved" ? "healthy" : "active"}>{titleCase(selected.status)}</Badge></div>
    <DetailTabs items={[{ id: "source", label: "Source" }, { id: "correlation", label: "Correlation" }]} value={detailTab} onChange={setDetailTab} />
    <DetailCard>{detailTab === "source" ? <DetailList><Detail label="Repository">{selected.resource.repository ?? "—"}</Detail><Detail label="Project">{selected.resource.project ?? "—"}</Detail><Detail label="Service">{selected.resource.service ?? "—"}</Detail><Detail label="Observed">{formatDate(selected.created_at, true)}</Detail></DetailList> : <DetailList><Detail label="Credential">{credentialName(selected.credential_id)}</Detail><Detail label="Provider">{selected.resource.provider ?? "—"}</Detail><Detail label="Candidates">{selected.candidates.length}</Detail><Detail label="Run">{selected.run_id ?? "Not started"}</Detail></DetailList>}</DetailCard>
  </div>

  return (
    <div className="page">
      <PageHeader eyebrow="Operations" title="Incidents" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search incidents, repositories, or services" onClear={() => { setSearch(""); setStatus("open") }} filters={[{ label: "Status", value: status, defaultValue: "open", onChange: (event) => setStatus(event.target.value), children: <><option value="open">Open incidents</option><option value="all">All incidents</option><option value="action-required">Action required</option><option value="rotation-started">Rotation started</option><option value="resolved">Resolved</option></> }]} />

      <div>
        <Table>
          <TableHeader><TableRow><TableHead>Source</TableHead><TableHead>Affected resource</TableHead><TableHead>Credential</TableHead><TableHead>Severity</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map((incident) => (
            <TableRow key={incident.id}>
              <TableCell><div className="font-medium">{titleCase(incident.source)}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{incident.source_event_id}</div></TableCell>
              <TableCell><div>{incident.resource.service ?? incident.resource.repository ?? "—"}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{incident.resource.project ?? incident.resource.repository ?? "—"}</div></TableCell>
              <TableCell>{credentialName(incident.credential_id)}</TableCell>
              <TableCell><Badge variant={severityVariant(incident.severity)}>{incident.severity}</Badge></TableCell>
              <TableCell><Badge variant={incident.status === "resolved" ? "healthy" : incident.status === "action-required" ? "warning" : "active"}>{titleCase(incident.status)}</Badge></TableCell>
              <TableCell className="pr-0"><div className="flex justify-end"><Button variant="ghost" size="sm" className="pr-1" onClick={() => { setSelected(incident); setDetailTab("source") }}>View details <ChevronRight className="size-3.5" /></Button></div></TableCell>
            </TableRow>
          ))}</TableBody>
        </Table>
      </div>

    </div>
  )
}
