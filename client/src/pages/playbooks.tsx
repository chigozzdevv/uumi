import { useMemo, useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { BookOpenText } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Playbook } from "../types"
import { api } from "../lib/api"
import { formatDate, providerName } from "../lib/format"

export function PlaybooksPage() {
  const [search, setSearch] = useState("")
  const [provider, setProvider] = useState("all")
  const [status, setStatus] = useState("all")
  const [selected, setSelected] = useState<Playbook | null>(null)
  const [playbooks, graph] = useQueries({ queries: [
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (playbooks.data ?? []).filter((playbook) => {
      const playbookStatus = playbook.active_version_id ? "active" : "draft"
      return (provider === "all" || playbook.provider === provider) && (status === "all" || playbookStatus === status) && (!term || `${playbook.name} ${playbook.provider}`.toLowerCase().includes(term))
    })
  }, [playbooks.data, provider, search, status])
  if ([playbooks, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [playbooks, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const assignments = (playbook: Playbook) => graph.data!.credentials.filter((credential) => credential.playbook_version === playbook.active_version_id)

  return (
    <div className="page">
      <PageHeader section="Governance · Playbooks" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search playbooks or providers" filters={[{ label: "Provider", value: provider, onChange: (event) => setProvider(event.target.value), children: <><option value="all">All providers</option>{[...new Set(playbooks.data!.map((item) => item.provider))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</> }, { label: "Status", value: status, onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="active">Active</option><option value="draft">Draft</option></> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Playbook</TableHead><TableHead>Provider</TableHead><TableHead>Assigned</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader><TableBody>{rows.map((playbook) => <TableRow key={playbook.id} className="cursor-pointer" onClick={() => setSelected(playbook)}><TableCell><div className="flex items-center gap-3"><Marker icon={BookOpenText} tone="neutral" /><span className="font-medium">{playbook.name}</span></div></TableCell><TableCell><Provider value={playbook.provider} /></TableCell><TableCell>{assignments(playbook).length}</TableCell><TableCell>{playbook.latest_version}</TableCell><TableCell><Badge variant={playbook.active_version_id ? "healthy" : "warning"}>{playbook.active_version_id ? "Active" : "Draft"}</Badge></TableCell><TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(playbook.updated_at)}</TableCell></TableRow>)}</TableBody></Table></div>
      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.name ?? "Playbook"}>
        {selected && <><Section title="Playbook"><DetailList><Detail label="Provider"><Provider value={selected.provider} /></Detail><Detail label="Version">{selected.latest_version}</Detail><Detail label="Status"><Badge variant={selected.active_version_id ? "healthy" : "warning"}>{selected.active_version_id ? "Active" : "Draft"}</Badge></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section><Section title="Credentials"><div className="space-y-2">{assignments(selected).map((credential) => <div key={credential.id} className="rounded-xl border border-[var(--border-soft)] bg-white/65 p-4 text-[11px] font-semibold">{credential.display_name}</div>)}{assignments(selected).length === 0 && <div className="text-[10px] text-[var(--ink-muted)]">No assignments</div>}</div></Section></>}
      </Modal>
    </div>
  )
}
