import { useMemo, useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { Box, ChevronRight, GitBranch, Server } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Drawer } from "../components/ui/drawer"
import type { Application } from "../types"
import { api } from "../lib/api"
import { formatDate } from "../lib/format"

export function ApplicationsPage() {
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<Application | null>(null)
  const [applications, environments, graph] = useQueries({ queries: [
    { queryKey: ["applications"], queryFn: () => api.getApplications() },
    { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (applications.data ?? []).filter((item) => !term || `${item.display_name} ${item.repository_ids.join(" ")}`.toLowerCase().includes(term))
  }, [applications.data, search])

  if ([applications, environments, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [applications, environments, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selectedEnvironments = selected ? environments.data!.filter((item) => item.application_id === selected.id) : []
  const selectedServices = selected ? graph.data!.services.filter((item) => item.application_id === selected.id) : []

  return (
    <div className="page">
      <PageHeader section="Inventory · Applications" title="Applications" description="Operational structure connecting applications, environments, runtime services, and the credential generations each service consumes." />
      <Toolbar value={search} onChange={setSearch} placeholder="Search applications or repositories" />

      <div className="space-y-3">
        {filtered.map((application) => {
          const appEnvironments = environments.data!.filter((item) => item.application_id === application.id)
          const appServices = graph.data!.services.filter((item) => item.application_id === application.id)
          const credentialIds = new Set(graph.data!.bindings.filter((binding) => appServices.some((service) => service.id === binding.service_id)).map((binding) => binding.credential_id))
          return (
            <button key={application.id} className="panel focus-ring flex w-full items-center gap-5 px-5 py-5 text-left transition hover:border-[#c9c6d4] hover:bg-white/70" onClick={() => setSelected(application)}>
              <span className="grid size-10 shrink-0 place-items-center rounded-[13px] bg-[var(--accent-soft)] text-[var(--accent)]"><Box className="size-[18px]" /></span>
              <span className="min-w-0 flex-1"><span className="block text-[13px] font-semibold">{application.display_name}</span><span className="mt-1.5 flex items-center gap-1.5 truncate text-[10px] text-[var(--ink-muted)]"><GitBranch className="size-3" /> {application.repository_ids.join(" · ")}</span></span>
              <span className="hidden gap-10 text-right sm:flex">
                <span><span className="data-label block">Environments</span><span className="mt-1 block text-sm font-semibold">{appEnvironments.length}</span></span>
                <span><span className="data-label block">Services</span><span className="mt-1 block text-sm font-semibold">{appServices.length}</span></span>
                <span><span className="data-label block">Credentials</span><span className="mt-1 block text-sm font-semibold">{credentialIds.size}</span></span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-[var(--ink-muted)]" />
            </button>
          )
        })}
      </div>

      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Application"} subtitle={selected?.id}>
        {selected && <>
          <Section title="Application"><DetailList><Detail label="Repositories">{selected.repository_ids.join(", ")}</Detail><Detail label="Last updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Revision">{selected.revision}</Detail></DetailList></Section>
          <Section title="Environments"><div className="space-y-2">{selectedEnvironments.map((environment) => {
            const services = selectedServices.filter((service) => service.environment_id === environment.id)
            return <div key={environment.id} className="rounded-xl border border-[var(--border-soft)] bg-white/60 p-4"><div className="flex items-center justify-between"><div className="text-[11px] font-semibold">{environment.display_name}</div><Badge variant={environment.production ? "active" : "neutral"}>{environment.production ? "Production" : "Non-production"}</Badge></div><div className="mt-2 text-[10px] text-[var(--ink-soft)]">{environment.region} · {services.length} services</div></div>
          })}</div></Section>
          <Section title="Runtime services"><div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedServices.map((service) => {
            const credentials = graph.data!.bindings.filter((binding) => binding.service_id === service.id).length
            return <div key={service.id} className="flex items-center gap-3 py-3.5"><span className="grid size-7 place-items-center rounded-lg bg-[#ececea] text-[var(--ink-soft)]"><Server className="size-3.5" /></span><div className="min-w-0 flex-1"><div className="text-[11px] font-semibold">{service.display_name}</div><div className="mono mt-1 truncate text-[9px] text-[var(--ink-muted)]">{service.runtime_resource}</div></div><span className="text-[10px] text-[var(--ink-soft)]">{credentials} credential{credentials === 1 ? "" : "s"}</span></div>
          })}</div></Section>
        </>}
      </Drawer>
    </div>
  )
}
