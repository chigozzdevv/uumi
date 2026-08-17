import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Drawer } from "../components/ui/drawer"
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
      <PageHeader section="System · Connections" title="Connections" description="Authorised provider, secret-store, runtime, telemetry, incident-source, and browser access boundaries." />
      <Toolbar value={search} onChange={setSearch} placeholder="Search connections or providers" filters={[{ label: "Kind", value: kind, onChange: (event) => setKind(event.target.value), children: <><option value="all">All connection types</option>{[...new Set(query.data!.map((item) => item.kind))].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</> }]} />
      <div className="panel overflow-hidden"><Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Provider</TableHead><TableHead>Kind</TableHead><TableHead>Capabilities</TableHead><TableHead>Region</TableHead><TableHead>Status</TableHead></TableRow></TableHeader><TableBody>{rows.map((connection) => <TableRow key={connection.id} className="cursor-pointer" onClick={() => setSelected(connection)}><TableCell><div className="font-semibold">{connection.display_name}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{connection.id}</div></TableCell><TableCell><Provider value={connection.provider} /></TableCell><TableCell className="text-[var(--ink-soft)]">{titleCase(connection.kind)}</TableCell><TableCell>{connection.capabilities.length}</TableCell><TableCell>{connection.region}</TableCell><TableCell><Badge variant={connection.status === "ready" ? "healthy" : connection.status === "reauthentication-required" ? "danger" : "warning"}>{titleCase(connection.status)}</Badge></TableCell></TableRow>)}</TableBody></Table></div>
      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Connection"} subtitle={selected?.id}>
        {selected && <><Section title="Connection"><DetailList><Detail label="Provider"><Provider value={selected.provider} /></Detail><Detail label="Kind">{titleCase(selected.kind)}</Detail><Detail label="Status"><Badge variant={selected.status === "ready" ? "healthy" : "danger"}>{titleCase(selected.status)}</Badge></Detail><Detail label="Region">{selected.region}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Revision">{selected.revision}</Detail></DetailList></Section><Section title="Capabilities"><div className="flex flex-wrap gap-2">{selected.capabilities.map((capability) => <Badge key={capability} variant="active" dot={false}>{capability}</Badge>)}</div></Section><Section title="Allowed resources"><div className="space-y-2">{selected.allowed_resources.map((resource) => <div key={resource} className="mono rounded-lg bg-white/70 px-3 py-2.5 text-[9px] text-[var(--ink-soft)]">{resource}</div>)}</div></Section><Section title="Authentication boundary"><p className="text-[10px] leading-5 text-[var(--ink-soft)]">Authentication is represented by an opaque reference. Raw management credentials, refresh tokens, session cookies, and MFA values are never exposed to agents or this dashboard.</p></Section></>}
      </Drawer>
    </div>
  )
}
