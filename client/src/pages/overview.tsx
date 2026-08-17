import { useQueries } from "@tanstack/react-query"
import { ArrowUpRight, Check, CircleAlert, Clock3, RotateCw } from "lucide-react"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Provider } from "../components/provider"
import type { NavItem } from "../components/sidebar"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

const stages = ["trigger", "preflight", "playbook", "create", "store", "deploy", "verify", "rollout", "observe", "approval", "revoke", "complete"]

export function OverviewPage({ onNavigate }: { onNavigate: (nav: NavItem) => void }) {
  const [summary, runs, incidents, approvals, graph] = useQueries({
    queries: [
      { queryKey: ["overview"], queryFn: () => api.getOverview() },
      { queryKey: ["rotations"], queryFn: () => api.getRotations() },
      { queryKey: ["incidents"], queryFn: () => api.getIncidents() },
      { queryKey: ["approvals"], queryFn: () => api.getApprovals() },
      { queryKey: ["graph"], queryFn: () => api.getGraph() },
    ],
  })

  const queries = [summary, runs, incidents, approvals, graph]
  if (queries.some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = queries.find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const values = summary.data!
  const activeRuns = runs.data!.filter((run) => ["pending", "running", "paused", "recovering"].includes(run.status))
  const urgentIncidents = incidents.data!.filter((incident) => !["resolved", "dismissed"].includes(incident.status))
  const pendingApprovals = approvals.data!.filter((approval) => approval.decision === "pending")
  const credential = (id: string) => graph.data!.credentials.find((item) => item.id === id)

  const metrics = [
    { label: "Managed credentials", value: values.credentials, detail: `${graph.data!.services.length} consuming services`, target: "credentials" as NavItem },
    { label: "Rotations in progress", value: values.rotations_in_progress, detail: "Durable workflow runs", target: "rotations" as NavItem },
    { label: "Open incidents", value: values.open_incidents, detail: "Ranked by confidence", target: "incidents" as NavItem },
    { label: "Awaiting approval", value: values.pending_approvals, detail: "Action-bound decisions", target: "approvals" as NavItem },
    { label: "Failed rotations", value: values.failed_rotations, detail: "Recovery available", target: "rotations" as NavItem },
  ]

  return (
    <div className="page">
      <PageHeader section="Overview" title="What needs attention" description="A current view of credential exposure, protected actions, and rotation progress across Acme Corporation." />

      <section className="mb-10 grid overflow-hidden rounded-[16px] border border-[var(--border)] bg-white/55 sm:grid-cols-2 xl:grid-cols-5">
        {metrics.map((metric, index) => (
          <button key={metric.label} className={`focus-ring p-5 text-left transition hover:bg-white ${index ? "border-t border-[var(--border-soft)] sm:border-l sm:border-t-0" : ""}`} onClick={() => onNavigate(metric.target)}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.065em] text-[var(--ink-muted)]">{metric.label}</div>
            <div className="mt-4 text-[2rem] font-semibold leading-none tracking-[-0.055em]">{metric.value}</div>
            <div className="mt-2 text-[10px] text-[var(--ink-soft)]">{metric.detail}</div>
          </button>
        ))}
      </section>

      <div className="grid gap-8 xl:grid-cols-[1.55fr_1fr]">
        <section>
          <div className="mb-4 flex items-end justify-between">
            <div><div className="eyebrow">Live operations</div><h2 className="mt-1 text-lg font-semibold tracking-[-0.035em]">Active rotations</h2></div>
            <button className="focus-ring flex items-center gap-1.5 rounded-lg px-2 py-1 text-[10px] font-semibold text-[var(--accent)] hover:bg-white/70" onClick={() => onNavigate("rotations")}>View all <ArrowUpRight className="size-3" /></button>
          </div>
          <div className="panel overflow-hidden divide-y divide-[var(--border-soft)]">
            {activeRuns.map((run) => {
              const item = credential(run.credential_id)
              const progress = Math.max(4, ((stages.indexOf(run.stage) + 1) / stages.length) * 100)
              return (
                <button key={run.id} className="focus-ring block w-full px-5 py-5 text-left transition hover:bg-white/65" onClick={() => onNavigate("rotations")}>
                  <div className="flex items-start gap-4">
                    <Provider value={item?.provider ?? "firekey"} label={false} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2"><span className="truncate text-[12px] font-semibold">{item?.display_name ?? run.credential_id}</span><Badge variant={run.status === "paused" ? "warning" : "active"}>{titleCase(run.status)}</Badge></div>
                      <p className="mt-1.5 truncate text-[10px] text-[var(--ink-soft)]">{run.trigger.reason}</p>
                      <div className="mt-4 h-1 overflow-hidden rounded-full bg-[#e3e3e0]"><div className="h-full rounded-full bg-[var(--accent)] transition-all" style={{ width: `${progress}%` }} /></div>
                      <div className="mt-2 flex justify-between text-[9px] font-medium uppercase tracking-[0.06em] text-[var(--ink-muted)]"><span>{titleCase(run.stage)}</span><span>{Math.round(progress)}%</span></div>
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </section>

        <section>
          <div className="mb-4"><div className="eyebrow">Decision queue</div><h2 className="mt-1 text-lg font-semibold tracking-[-0.035em]">Requires attention</h2></div>
          <div className="panel overflow-hidden divide-y divide-[var(--border-soft)]">
            {urgentIncidents.slice(0, 2).map((incident) => (
              <button key={incident.id} className="focus-ring flex w-full gap-3 px-5 py-4 text-left hover:bg-white/65" onClick={() => onNavigate("incidents")}>
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-[var(--red-soft)] text-[var(--red)]"><CircleAlert className="size-3.5" /></span>
                <span className="min-w-0 flex-1"><span className="block text-[11px] font-semibold">{titleCase(incident.severity)} incident</span><span className="mt-1 block truncate text-[10px] text-[var(--ink-soft)]">{incident.source_event_id} · {incident.resource.service ?? incident.resource.repository}</span></span>
                <ArrowUpRight className="mt-1 size-3 text-[var(--ink-muted)]" />
              </button>
            ))}
            {pendingApprovals.slice(0, 2).map((approval) => (
              <button key={approval.id} className="focus-ring flex w-full gap-3 px-5 py-4 text-left hover:bg-white/65" onClick={() => onNavigate("approvals")}>
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-[var(--amber-soft)] text-[var(--amber)]"><Clock3 className="size-3.5" /></span>
                <span className="min-w-0 flex-1"><span className="block text-[11px] font-semibold">Protected action approval</span><span className="mt-1 block truncate text-[10px] text-[var(--ink-soft)]">{credential(runs.data!.find((run) => run.id === approval.run_id)?.credential_id ?? "")?.display_name ?? approval.run_id}</span></span>
                <ArrowUpRight className="mt-1 size-3 text-[var(--ink-muted)]" />
              </button>
            ))}
          </div>
        </section>
      </div>

      <section className="mt-10 flex flex-col gap-4 border-t border-[var(--border)] pt-5 text-[10px] text-[var(--ink-soft)] sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2"><span className="grid size-5 place-items-center rounded-full bg-[var(--green-soft)] text-[var(--green)]"><Check className="size-3" /></span> Audit chain verified through event 408</div>
        <div className="flex items-center gap-5"><span className="flex items-center gap-1.5"><RotateCw className="size-3" /> Last reconciled {formatDate("2026-08-16T18:00:00Z", true)}</span><span>Region us-central1</span></div>
      </section>
    </div>
  )
}
