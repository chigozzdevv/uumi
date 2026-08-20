import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Bot, ChevronRight } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { AgentRegistration } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

export function AgentsPage() {
  const [search, setSearch] = useState("")
  const [kind, setKind] = useState("all")
  const [status, setStatus] = useState("all")
  const [selected, setSelected] = useState<AgentRegistration | null>(null)
  const query = useQuery({ queryKey: ["agents"], queryFn: () => api.getAgents() })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (query.data ?? []).filter((agent) => (kind === "all" || agent.kind === kind) && (status === "all" || agent.status === status) && (!term || `${agent.display_name} ${agent.owner} ${agent.kind}`.toLowerCase().includes(term)))
  }, [kind, query.data, search, status])
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  if (selected) return <div className="page"><PageHeader eyebrow="Management / Agent fleet" title={selected.display_name} onBack={() => setSelected(null)} /><div className="grid gap-5 xl:grid-cols-2"><Section title="Agent"><DetailList><Detail label="Type">{titleCase(selected.kind)}</Detail><Detail label="Status"><Badge variant={selected.status === "ready" ? "healthy" : "danger"}>{titleCase(selected.status)}</Badge></Detail><Detail label="Version">{selected.version}</Detail><Detail label="Region">{selected.region}</Detail><Detail label="Owner">{selected.owner}</Detail><Detail label="Registered">{formatDate(selected.registered_at, true)}</Detail></DetailList></Section><Section title="Skills"><div className="flex flex-wrap gap-2">{selected.skills.map((skill) => <Badge key={skill} variant="neutral">{titleCase(skill)}</Badge>)}</div></Section></div></div>

  return (
    <div className="page">
      <PageHeader eyebrow="Management" title="Agent fleet" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search agents" resultCount={rows.length} resultLabel="agents" onClear={() => { setSearch(""); setKind("all"); setStatus("all") }} filters={[{ label: "Type", value: kind, defaultValue: "all", onChange: (event) => setKind(event.target.value), children: <><option value="all">All agent types</option>{[...new Set(query.data!.map((item) => item.kind))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }, { label: "Status", value: status, defaultValue: "all", onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option>{[...new Set(query.data!.map((item) => item.status))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
      <Table><TableHeader><TableRow><TableHead>Agent</TableHead><TableHead>Type</TableHead><TableHead>Version</TableHead><TableHead>Region</TableHead><TableHead>Status</TableHead><TableHead className="w-36">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((agent) => <TableRow key={agent.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={() => setSelected(agent)}><Marker icon={Bot} />{agent.display_name}</button></TableCell><TableCell>{titleCase(agent.kind)}</TableCell><TableCell>{agent.version}</TableCell><TableCell>{agent.region}</TableCell><TableCell><Badge variant={agent.status === "ready" ? "healthy" : agent.status === "degraded" ? "danger" : "neutral"}>{titleCase(agent.status)}</Badge></TableCell><TableCell><Button variant="ghost" size="sm" onClick={() => setSelected(agent)}>View details <ChevronRight className="size-3.5" /></Button></TableCell></TableRow>)}</TableBody></Table>
    </div>
  )
}
