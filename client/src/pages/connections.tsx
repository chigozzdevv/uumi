import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { PlugZap } from "lucide-react"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Detail, DetailList, Section } from "../components/detail"
import type { Connection } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

export function ConnectionsPage() {
  const [search, setSearch] = useState("")
  const [kind, setKind] = useState("all")
  const [selected, setSelected] = useState<Connection | null>(null)
  const query = useQuery({ queryKey: ["connections"], queryFn: () => api.getConnections() })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (query.data ?? []).filter((item) => (kind === "all" || item.kind === kind) && (!term || `${item.display_name} ${item.provider} ${item.kind}`.toLowerCase().includes(term)))
  }, [kind, query.data, search])
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  return (
    <div className="page">
      <PageHeader section="System · Connections" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search connections or providers" filters={[{ label: "Kind", value: kind, onChange: (event) => setKind(event.target.value), children: <><option value="all">All connection types</option>{[...new Set(query.data!.map((item) => item.kind))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Provider</TableHead><TableHead>Type</TableHead><TableHead>Region</TableHead><TableHead>Status</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => <TableRow key={connection.id} className="cursor-pointer" onClick={() => setSelected(connection)}><TableCell><div className="flex items-center gap-3"><Marker icon={PlugZap} tone="blue" /><span className="font-medium">{connection.display_name}</span></div></TableCell><TableCell><Provider value={connection.provider} /></TableCell><TableCell className="text-[var(--ink-soft)]">{titleCase(connection.kind)}</TableCell><TableCell>{connection.region}</TableCell><TableCell><Badge variant={connection.status === "ready" ? "healthy" : connection.status === "reauthentication-required" ? "danger" : "warning"}>{titleCase(connection.status)}</Badge></TableCell></TableRow>)}</TableBody></Table></div>
      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Connection"}>
        {selected && <Section title="Connection"><DetailList><Detail label="Provider"><Provider value={selected.provider} /></Detail><Detail label="Type">{titleCase(selected.kind)}</Detail><Detail label="Status"><Badge variant={selected.status === "ready" ? "healthy" : "danger"}>{titleCase(selected.status)}</Badge></Detail><Detail label="Region">{selected.region}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section>}
      </Modal>
    </div>
  )
}
