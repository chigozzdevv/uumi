import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Check, Fingerprint } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Drawer } from "../components/ui/drawer"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { AuditEvent } from "../types"
import { api } from "../lib/api"
import { formatDate, shortId, titleCase } from "../lib/format"

export function AuditsPage() {
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const query = useQuery({ queryKey: ["audits"], queryFn: () => api.getAudits() })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (query.data ?? []).filter((item) => !term || `${item.kind} ${item.actor_id} ${item.resource} ${item.run_id}`.toLowerCase().includes(term))
  }, [query.data, search])
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  return (
    <div className="page">
      <PageHeader section="System · Audit" title="Audit" description="The canonical, region-bound hash chain for every security-relevant action, decision, and supporting evidence reference." actions={<Badge variant="healthy"><Check className="size-3" /> Chain verified</Badge>} />
      <Toolbar value={search} onChange={setSearch} placeholder="Search events, actors, resources, or runs" />
      <div className="panel overflow-hidden"><Table><TableHeader><TableRow><TableHead>Sequence</TableHead><TableHead>Time</TableHead><TableHead>Event</TableHead><TableHead>Actor</TableHead><TableHead>Resource</TableHead><TableHead>Event hash</TableHead></TableRow></TableHeader><TableBody>{rows.map((event) => <TableRow key={event.id} className="cursor-pointer" onClick={() => setSelected(event)}><TableCell className="mono text-[10px] text-[var(--ink-muted)]">#{event.sequence}</TableCell><TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(event.occurred_at, true)}</TableCell><TableCell><div className="font-semibold">{titleCase(event.kind)}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{event.id}</div></TableCell><TableCell className="mono text-[9px] text-[var(--ink-soft)]">{event.actor_id}</TableCell><TableCell><div className="max-w-[260px] truncate text-[10px]">{event.resource}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{event.run_id}</div></TableCell><TableCell><span className="inline-flex items-center gap-1.5 font-mono text-[9px] text-[var(--green)]"><Fingerprint className="size-3" />{shortId(event.event_hash, 12)}</span></TableCell></TableRow>)}</TableBody></Table></div>
      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? titleCase(selected.kind) : "Audit event"} subtitle={selected?.id}>
        {selected && <><Section title="Record"><DetailList><Detail label="Sequence">#{selected.sequence}</Detail><Detail label="Actor"><span className="mono text-[9px]">{selected.actor_id}</span></Detail><Detail label="Resource"><span className="mono text-[9px]">{selected.resource}</span></Detail><Detail label="Run"><span className="mono text-[9px]">{selected.run_id ?? "—"}</span></Detail><Detail label="Occurred">{formatDate(selected.occurred_at, true)}</Detail><Detail label="Region">{selected.region}</Detail></DetailList></Section><Section title="Chain"><DetailList><Detail label="Previous hash"><span className="mono text-[9px]">{shortId(selected.previous_hash, 28)}</span></Detail><Detail label="Event hash"><span className="mono text-[9px]">{shortId(selected.event_hash, 28)}</span></Detail><Detail label="Evidence">{selected.evidence_ids.join(", ")}</Detail></DetailList></Section><Section title="Redacted payload"><div className="rounded-xl bg-[#20202c] p-4 font-mono text-[9px] leading-5 text-[#d9d7e3]">{JSON.stringify(selected.payload, null, 2)}</div></Section></>}
      </Drawer>
    </div>
  )
}
