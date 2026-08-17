import { useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { BookOpenText, ChevronRight } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Drawer } from "../components/ui/drawer"
import type { Playbook } from "../types"
import { api } from "../lib/api"
import { formatDate } from "../lib/format"

export function PlaybooksPage() {
  const [selected, setSelected] = useState<Playbook | null>(null)
  const [playbooks, graph] = useQueries({ queries: [
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  if ([playbooks, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [playbooks, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const assignments = (playbook: Playbook) => graph.data!.credentials.filter((credential) => credential.playbook_version === playbook.active_version_id)

  return (
    <div className="page">
      <PageHeader section="Governance · Playbooks" title="Playbooks" description="Approved, reusable operational methods for creating, transferring, verifying, deploying, recovering, and revoking credentials." />
      <div className="panel overflow-hidden divide-y divide-[var(--border-soft)]">
        {playbooks.data!.map((playbook) => <button key={playbook.id} className="focus-ring flex w-full items-center gap-4 px-5 py-5 text-left hover:bg-white/65" onClick={() => setSelected(playbook)}><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[#ececea] text-[var(--ink-soft)]"><BookOpenText className="size-4" /></span><span className="min-w-0 flex-1"><span className="block text-[12px] font-semibold">{playbook.name}</span><span className="mono mt-1.5 block text-[9px] text-[var(--ink-muted)]">{playbook.id}</span></span><span className="hidden w-40 sm:block"><Provider value={playbook.provider} /></span><span className="hidden text-right md:block"><span className="data-label block">Assigned</span><span className="mt-1.5 block text-[11px] font-semibold">{assignments(playbook).length} credentials</span></span><Badge variant={playbook.active_version_id ? "healthy" : "warning"}>{playbook.active_version_id ? "Active" : "Draft"}</Badge><ChevronRight className="size-4 text-[var(--ink-muted)]" /></button>)}
      </div>
      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.name ?? "Playbook"} subtitle={selected?.id}>
        {selected && <><Section title="Definition"><DetailList><Detail label="Provider"><Provider value={selected.provider} /></Detail><Detail label="Active version"><span className="mono text-[9px]">{selected.active_version_id ?? "—"}</span></Detail><Detail label="Latest version">{selected.latest_version}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Revision">{selected.revision}</Detail></DetailList></Section><Section title="Assignments"><div className="space-y-2">{assignments(selected).map((credential) => <div key={credential.id} className="rounded-xl border border-[var(--border-soft)] bg-white/65 p-4"><div className="text-[11px] font-semibold">{credential.display_name}</div><div className="mono mt-1 text-[9px] text-[var(--ink-muted)]">{credential.id}</div></div>)}{assignments(selected).length === 0 && <div className="text-[10px] text-[var(--ink-muted)]">No active credential assignments.</div>}</div></Section><Section title="Activation contract"><p className="text-[10px] leading-5 text-[var(--ink-soft)]">An active version is immutable and records its approved dry run. Browser playbooks additionally bind allowed domains, deterministic selectors, safe checkpoints, protected actions, and declared Secure Capture fields.</p></Section></>}
      </Drawer>
    </div>
  )
}
