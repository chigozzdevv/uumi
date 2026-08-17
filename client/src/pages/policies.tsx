import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronRight, ShieldCheck } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Drawer } from "../components/ui/drawer"
import type { Policy } from "../types"
import { api } from "../lib/api"
import { formatDate } from "../lib/format"

export function PoliciesPage() {
  const [selected, setSelected] = useState<Policy | null>(null)
  const query = useQuery({ queryKey: ["policies"], queryFn: () => api.getPolicies() })
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  return (
    <div className="page">
      <PageHeader section="Governance · Policies" title="Policies" description="Deterministic rules for when FireKey may act, what evidence it must collect, and where human authority is required." />
      <div className="panel overflow-hidden divide-y divide-[var(--border-soft)]">
        {query.data!.map((policy) => <button key={policy.id} className="focus-ring flex w-full items-center gap-4 px-5 py-5 text-left hover:bg-white/65" onClick={() => setSelected(policy)}><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><ShieldCheck className="size-4" /></span><span className="min-w-0 flex-1"><span className="block text-[12px] font-semibold">{policy.name}</span><span className="mono mt-1.5 block text-[9px] text-[var(--ink-muted)]">{policy.id}</span></span><span className="hidden text-right sm:block"><span className="data-label block">Active version</span><span className="mono mt-1.5 block text-[10px] font-semibold">{policy.active_version_id ?? "Draft"}</span></span><Badge variant={policy.active_version_id ? "healthy" : "warning"}>{policy.active_version_id ? "Active" : "Draft"}</Badge><ChevronRight className="size-4 text-[var(--ink-muted)]" /></button>)}
      </div>
      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.name ?? "Policy"} subtitle={selected?.id}>
        {selected && <><Section title="Version control"><DetailList><Detail label="Active version"><span className="mono text-[9px]">{selected.active_version_id ?? "—"}</span></Detail><Detail label="Latest version">{selected.latest_version}</Detail><Detail label="State"><Badge variant={selected.active_version_id ? "healthy" : "warning"}>{selected.active_version_id ? "Active" : "Draft"}</Badge></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Revision">{selected.revision}</Detail></DetailList></Section><Section title="Safety boundary"><p className="text-[10px] leading-5 text-[var(--ink-soft)]">The active immutable version defines required checks for all twelve stages, allowed and protected tools, observation limits, generation telemetry, trigger confidence, and authorised recovery modes.</p></Section></>}
      </Drawer>
    </div>
  )
}
