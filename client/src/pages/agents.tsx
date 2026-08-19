import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Bot } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Modal } from "../components/ui/modal"
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

  return (
    <div className="page">
      <PageHeader section="Governance · Agent Fleet" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search agents" filters={[{ label: "Type", value: kind, onChange: (event) => setKind(event.target.value), children: <><option value="all">All agent types</option>{[...new Set(query.data!.map((item) => item.kind))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }, { label: "Status", value: status, onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option>{[...new Set(query.data!.map((item) => item.status))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Agent</TableHead><TableHead>Type</TableHead><TableHead>Version</TableHead><TableHead>Region</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Registered</TableHead></TableRow></TableHeader><TableBody>{rows.map((agent) => <TableRow key={agent.id} className="cursor-pointer" onClick={() => setSelected(agent)}><TableCell><div className="flex items-center gap-3"><Marker icon={Bot} /><span className="font-medium">{agent.display_name}</span></div></TableCell><TableCell>{titleCase(agent.kind)}</TableCell><TableCell>{agent.version}</TableCell><TableCell>{agent.region}</TableCell><TableCell><Badge variant={agent.status === "ready" ? "healthy" : agent.status === "degraded" ? "danger" : "neutral"}>{titleCase(agent.status)}</Badge></TableCell><TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(agent.registered_at)}</TableCell></TableRow>)}</TableBody></Table></div>
      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Agent"}>
        {selected && <><Section title="Agent"><DetailList><Detail label="Type">{titleCase(selected.kind)}</Detail><Detail label="Status"><Badge variant={selected.status === "ready" ? "healthy" : "danger"}>{titleCase(selected.status)}</Badge></Detail><Detail label="Version">{selected.version}</Detail><Detail label="Region">{selected.region}</Detail><Detail label="Owner">{selected.owner}</Detail><Detail label="Registered">{formatDate(selected.registered_at, true)}</Detail></DetailList></Section><Section title="Skills"><div className="flex flex-wrap gap-2">{selected.skills.map((skill) => <Badge key={skill} variant="active">{titleCase(skill)}</Badge>)}</div></Section></>}
      </Modal>
    </div>
  )
}
