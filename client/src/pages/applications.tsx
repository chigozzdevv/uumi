import { useMemo, useState } from "react"
import { useQueries } from "@tanstack/react-query"
import { AppWindow, Server } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Application } from "../types"
import { api } from "../lib/api"
import { formatDate } from "../lib/format"

export function ApplicationsPage() {
  const [search, setSearch] = useState("")
  const [environment, setEnvironment] = useState("all")
  const [selected, setSelected] = useState<Application | null>(null)
  const [applications, environments, graph] = useQueries({ queries: [
    { queryKey: ["applications"], queryFn: () => api.getApplications() },
    { queryKey: ["environments"], queryFn: () => api.getEnvironments() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (applications.data ?? []).filter((item) => {
      const appEnvironments = (environments.data ?? []).filter((entry) => entry.application_id === item.id)
      const matchesEnvironment = environment === "all" || (environment === "production" ? appEnvironments.some((entry) => entry.production) : appEnvironments.some((entry) => !entry.production))
      return matchesEnvironment && (!term || `${item.display_name} ${item.repository_ids.join(" ")}`.toLowerCase().includes(term))
    })
  }, [applications.data, environment, environments.data, search])

  if ([applications, environments, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [applications, environments, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selectedEnvironments = selected ? environments.data!.filter((item) => item.application_id === selected.id) : []
  const selectedServices = selected ? graph.data!.services.filter((item) => item.application_id === selected.id) : []

  return (
    <div className="page">
      <PageHeader section="Inventory · Applications" />
      <Toolbar value={search} onChange={setSearch} placeholder="Search applications or repositories" filters={[{ label: "Environment", value: environment, onChange: (event) => setEnvironment(event.target.value), children: <><option value="all">All environments</option><option value="production">Production</option><option value="non-production">Non-production</option></> }]} />

      <div><Table><TableHeader><TableRow><TableHead>Application</TableHead><TableHead>Environments</TableHead><TableHead>Services</TableHead><TableHead>Credentials</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader><TableBody>{filtered.map((application) => {
        const appEnvironments = environments.data!.filter((item) => item.application_id === application.id)
        const appServices = graph.data!.services.filter((item) => item.application_id === application.id)
        const credentialIds = new Set(graph.data!.bindings.filter((binding) => appServices.some((service) => service.id === binding.service_id)).map((binding) => binding.credential_id))
        return <TableRow key={application.id} className="cursor-pointer" onClick={() => setSelected(application)}><TableCell><div className="flex items-center gap-3"><Marker icon={AppWindow} /><span className="font-medium">{application.display_name}</span></div></TableCell><TableCell>{appEnvironments.length}</TableCell><TableCell>{appServices.length}</TableCell><TableCell>{credentialIds.size}</TableCell><TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(application.updated_at)}</TableCell></TableRow>
      })}</TableBody></Table></div>

      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.display_name ?? "Application"}>
        {selected && <>
          <Section title="Application"><DetailList><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Environments">{selectedEnvironments.length}</Detail><Detail label="Services">{selectedServices.length}</Detail></DetailList></Section>
          <Section title="Environments"><div className="space-y-2">{selectedEnvironments.map((environment) => {
            const services = selectedServices.filter((service) => service.environment_id === environment.id)
            return <div key={environment.id} className="rounded-xl border border-[var(--border-soft)] bg-white/60 p-4"><div className="flex items-center justify-between"><div className="text-[11px] font-semibold">{environment.display_name}</div><Badge variant={environment.production ? "active" : "neutral"}>{environment.production ? "Production" : "Non-production"}</Badge></div><div className="mt-2 text-[10px] text-[var(--ink-soft)]">{environment.region} · {services.length} services</div></div>
          })}</div></Section>
          <Section title="Runtime services"><div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedServices.map((service) => {
            const credentials = graph.data!.bindings.filter((binding) => binding.service_id === service.id).length
            return <div key={service.id} className="flex items-center gap-3 py-3.5"><span className="grid size-7 place-items-center rounded-lg bg-[#ececea] text-[var(--ink-soft)]"><Server className="size-3.5" /></span><div className="min-w-0 flex-1 text-[11px] font-semibold">{service.display_name}</div><span className="text-[10px] text-[var(--ink-soft)]">{credentials} credential{credentials === 1 ? "" : "s"}</span></div>
          })}</div></Section>
        </>}
      </Modal>
    </div>
  )
}
