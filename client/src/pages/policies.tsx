import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ShieldCheck } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Policy } from "../types"
import { api } from "../lib/api"
import { formatDate } from "../lib/format"

export function PoliciesPage() {
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("all")
  const [selected, setSelected] = useState<Policy | null>(null)
  const query = useQuery({ queryKey: ["policies"], queryFn: () => api.getPolicies() })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (query.data ?? []).filter((policy) => {
      const policyStatus = policy.active_version_id ? "active" : "draft"
      return (status === "all" || policyStatus === status) && (!term || policy.name.toLowerCase().includes(term))
    })
  }, [query.data, search, status])
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  return (
    <div className="page">
      <PageHeader section="Governance · Policies" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search policies" filters={[{ label: "Status", value: status, onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="active">Active</option><option value="draft">Draft</option></> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Policy</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader><TableBody>{rows.map((policy) => <TableRow key={policy.id} className="cursor-pointer" onClick={() => setSelected(policy)}><TableCell><div className="flex items-center gap-3"><Marker icon={ShieldCheck} /><span className="font-medium">{policy.name}</span></div></TableCell><TableCell>{policy.latest_version}</TableCell><TableCell><Badge variant={policy.active_version_id ? "healthy" : "warning"}>{policy.active_version_id ? "Active" : "Draft"}</Badge></TableCell><TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(policy.updated_at)}</TableCell></TableRow>)}</TableBody></Table></div>
      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.name ?? "Policy"}>
        {selected && <Section title="Policy"><DetailList><Detail label="Version">{selected.latest_version}</Detail><Detail label="Status"><Badge variant={selected.active_version_id ? "healthy" : "warning"}>{selected.active_version_id ? "Active" : "Draft"}</Badge></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section>}
      </Modal>
    </div>
  )
}
