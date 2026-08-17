import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Bot, ChevronRight } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Drawer } from "../components/ui/drawer"
import type { AgentRegistration } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

export function AgentsPage() {
  const [selected, setSelected] = useState<AgentRegistration | null>(null)
  const query = useQuery({ queryKey: ["agents"], queryFn: () => api.getAgents() })
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  return (
    <div className="page">
      <PageHeader section="Governance · Agent Fleet" title="Agent Fleet" description="Four separately deployed institutional agents, registered capabilities, approved callers, and constrained tool destinations." />
      <div className="panel overflow-hidden divide-y divide-[var(--border-soft)]">
        {query.data!.map((agent) => <button key={agent.id} className="focus-ring flex w-full items-center gap-4 px-5 py-5 text-left hover:bg-white/65" onClick={() => setSelected(agent)}><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><Bot className="size-4" /></span><span className="min-w-0 flex-1"><span className="block text-[12px] font-semibold">{agent.display_name}</span><span className="mt-1.5 block text-[9px] text-[var(--ink-muted)]">{agent.skills.slice(0, 3).map(titleCase).join(" · ")}</span></span><span className="hidden text-right sm:block"><span className="data-label block">Version</span><span className="mono mt-1.5 block text-[10px]">{agent.version}</span></span><span className="hidden text-right md:block"><span className="data-label block">Region</span><span className="mt-1.5 block text-[10px]">{agent.region}</span></span><Badge variant={agent.status === "ready" ? "healthy" : agent.status === "degraded" ? "danger" : "neutral"}>{titleCase(agent.status)}</Badge><ChevronRight className="size-4 text-[var(--ink-muted)]" /></button>)}
      </div>
      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Agent"} subtitle={selected?.id}>
        {selected && <><Section title="Registry"><DetailList><Detail label="Kind">{titleCase(selected.kind)}</Detail><Detail label="Status"><Badge variant={selected.status === "ready" ? "healthy" : "danger"}>{titleCase(selected.status)}</Badge></Detail><Detail label="Version"><span className="mono text-[9px]">{selected.version}</span></Detail><Detail label="Region">{selected.region}</Detail><Detail label="Registered">{formatDate(selected.registered_at, true)}</Detail></DetailList></Section><Section title="Identity"><DetailList><Detail label="Owner">{selected.owner}</Detail><Detail label="Service identity"><span className="mono text-[9px]">{selected.identity}</span></Detail><Detail label="Deployment"><span className="mono text-[9px]">{selected.deployment}</span></Detail><Detail label="Registry"><span className="mono text-[9px]">{selected.registry}</span></Detail></DetailList></Section><Section title="Registered skills"><div className="flex flex-wrap gap-2">{selected.skills.map((skill) => <Badge key={skill} variant="active" dot={false}>{titleCase(skill)}</Badge>)}</div></Section><Section title="Execution boundary"><DetailList><Detail label="Approved callers">{selected.approved_callers.length}</Detail><Detail label="Tool destinations">{selected.tool_destinations.join(", ")}</Detail><Detail label="Ingress"><span className="mono text-[9px]">{selected.ingress_gateway}</span></Detail><Detail label="Egress"><span className="mono text-[9px]">{selected.egress_gateway}</span></Detail></DetailList></Section></>}
      </Drawer>
    </div>
  )
}
