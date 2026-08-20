import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { Check, Circle, Clock3, Plus, RotateCw, TriangleAlert } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Field, formControl } from "../components/workspace"
import type { RotationRun, StageName } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

const stages: Array<{ id: StageName; label: string }> = [
  { id: "trigger", label: "Trigger" },
  { id: "preflight", label: "Preflight" },
  { id: "plan", label: "Plan" },
  { id: "create", label: "Create" },
  { id: "store", label: "Store" },
  { id: "deploy", label: "Deploy" },
  { id: "verify", label: "Verify" },
  { id: "rollout", label: "Rollout" },
  { id: "observe", label: "Observe" },
  { id: "approval", label: "Approval" },
  { id: "revoke", label: "Revoke" },
  { id: "complete", label: "Complete" },
]

function variant(run: RotationRun) {
  if (["failed", "cleanup-required"].includes(run.status)) return "danger" as const
  if (run.status === "paused") return "warning" as const
  if (run.status === "completed") return "healthy" as const
  return "active" as const
}

export function RotationsPage({ activeRunId, onNavigateApproval }: { activeRunId?: string; onNavigateApproval: () => void }) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState("active")
  const [selectedId, setSelectedId] = useState(activeRunId ?? "")
  const [creating, setCreating] = useState(false)
  const [credentialId, setCredentialId] = useState("")
  const [reason, setReason] = useState("Routine credential rotation")
  const [urgency, setUrgency] = useState<"routine" | "urgent" | "emergency">("routine")
  const [runs, graph] = useQueries({ queries: [
    { queryKey: ["rotations"], queryFn: () => api.getRotations() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  const create = useMutation({ mutationFn: () => {
    const selectedCredential = graph.data!.credentials.find((item) => item.id === credentialId)!
    return api.startRotation({ credential_id: selectedCredential.id, policy_version: selectedCredential.policy_version, reason: reason.trim(), urgency })
  }, onSuccess: async (run) => { await queryClient.invalidateQueries({ queryKey: ["rotations"] }); setSelectedId(run.id); setFilter("active"); setCreating(false) } })

  useEffect(() => { if (activeRunId) setSelectedId(activeRunId) }, [activeRunId])

  const filtered = useMemo(() => (runs.data ?? []).filter((run) => {
    if (filter === "active") return ["pending", "running", "paused", "recovering"].includes(run.status)
    if (filter === "completed") return ["completed", "compensated"].includes(run.status)
    if (filter === "failed") return ["failed", "cleanup-required"].includes(run.status)
    return run.trigger.source === "scheduler" && run.status === "pending"
  }), [filter, runs.data])

  if ([runs, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [runs, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selected = runs.data!.find((run) => run.id === selectedId) ?? filtered[0] ?? runs.data![0]
  const credential = graph.data!.credentials.find((item) => item.id === selected?.credential_id)
  const stageIndex = stages.findIndex((stage) => stage.id === selected?.stage)
  const availableCredentials = graph.data!.credentials.filter((item) => !runs.data!.some((run) => run.credential_id === item.id && !["completed", "compensated", "failed", "cleanup-required"].includes(run.status)))

  function openCreate() {
    setCredentialId(availableCredentials[0]?.id ?? "")
    setReason("Routine credential rotation")
    setUrgency("routine")
    create.reset()
    setCreating(true)
  }

  return (
    <div className="page">
      <PageHeader title="Rotations" description="Follow active runs across FireKey’s twelve evidence-gated stages." actions={<Button onClick={openCreate} disabled={!availableCredentials.length}><Plus className="size-3.5" /> Start rotation</Button>} />
      <div className="mb-6 flex gap-1 border-b border-[var(--border)]">
        {["active", "scheduled", "completed", "failed"].map((item) => <button key={item} className={`focus-ring -mb-px border-b-2 px-4 pb-3 text-[10px] font-semibold capitalize ${filter === item ? "border-[var(--accent)] text-[var(--accent)]" : "border-transparent text-[var(--ink-muted)] hover:text-[var(--ink)]"}`} onClick={() => setFilter(item)}>{titleCase(item)}</button>)}
      </div>

      <div className="grid gap-5 xl:grid-cols-[300px_1fr]">
        <div className="space-y-2">
          {filtered.length === 0 && <div className="panel p-8 text-center text-[11px] text-[var(--ink-muted)]">No runs in this view.</div>}
          {filtered.map((run) => {
            const item = graph.data!.credentials.find((entry) => entry.id === run.credential_id)
            return <button key={run.id} className={`focus-ring w-full rounded-[15px] border p-4 text-left transition ${selected?.id === run.id ? "border-[var(--ink)] bg-white" : "border-[var(--border-soft)] bg-white/40 hover:bg-white/70"}`} onClick={() => setSelectedId(run.id)}><div className="flex items-start gap-3"><Provider value={item?.provider ?? "firekey"} label={false} /><div className="min-w-0 flex-1 truncate text-[11px] font-semibold">{item?.display_name ?? "Credential"}</div><Badge variant={variant(run)}>{titleCase(run.status)}</Badge></div><div className="mt-4 flex items-center justify-between text-[9px] text-[var(--ink-soft)]"><span>{titleCase(run.stage)}</span><span>{formatDate(run.updated_at, true)}</span></div></button>
          })}
        </div>

        {selected && <section className="panel overflow-hidden">
          <header className="flex flex-col gap-5 border-b border-[var(--border)] p-6 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-[16px] font-semibold tracking-[-0.035em]">{credential?.display_name ?? "Credential"}</h2><Badge variant={variant(selected)}>{titleCase(selected.status)}</Badge><Badge variant={selected.trigger.urgency === "emergency" ? "danger" : "neutral"}>{selected.trigger.urgency}</Badge></div></div>
            {selected.status === "paused" && selected.stage === "approval" && <Button onClick={onNavigateApproval}>Review approval <Clock3 className="size-3.5" /></Button>}
          </header>

          <div className="grid 2xl:grid-cols-[1fr_280px]">
            <div className="p-6">
              <div className="eyebrow mb-5">Run lifecycle</div>
              <div>{stages.map((stage, index) => {
                const completed = index < stageIndex || selected.status === "completed"
                const current = index === stageIndex && selected.status !== "completed"
                const failed = current && ["failed", "cleanup-required"].includes(selected.status)
                return <div key={stage.id} className="relative flex gap-4 pb-5 last:pb-0">{index < stages.length - 1 && <span className={`absolute left-[11px] top-6 h-[calc(100%-8px)] w-px ${completed ? "bg-[var(--green)]" : "bg-[var(--border)]"}`} />}<span className={`relative z-10 grid size-[23px] shrink-0 place-items-center rounded-full border ${completed ? "border-[var(--green)] bg-[var(--green)] text-white" : failed ? "border-[var(--red)] bg-[var(--red-soft)] text-[var(--red)]" : current ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-[var(--border)] bg-[var(--workspace)] text-[var(--ink-muted)]"}`}>{completed ? <Check className="size-3" /> : failed ? <TriangleAlert className="size-3" /> : current ? <RotateCw className={`size-3 ${selected.status === "running" ? "animate-spin" : ""}`} /> : <Circle className="size-2" />}</span><div className="pt-0.5"><div className={`text-[11px] font-semibold ${current ? "text-[var(--ink)]" : completed ? "text-[var(--ink-soft)]" : "text-[var(--ink-muted)]"}`}>{stage.label}</div>{failed && selected.failure && <div className="mt-1 text-[9px] text-[var(--red)]">{selected.failure.message}</div>}</div></div>
              })}</div>
            </div>

            <aside className="border-t border-[var(--border)] bg-white/35 p-6 2xl:border-l 2xl:border-t-0">
              <Section title="Activity"><DetailList><Detail label="Stage">{titleCase(selected.stage)}</Detail><Detail label="Source">{titleCase(selected.trigger.source)}</Detail><Detail label="Started">{formatDate(selected.trigger.received_at, true)}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section>
            </aside>
          </div>
        </section>}
      </div>
      <Modal isOpen={creating} onClose={() => setCreating(false)} title="Start rotation" description="Start a manual run using the policy already assigned to the credential." actions={<Button onClick={() => create.mutate()} disabled={!credentialId || !reason.trim() || create.isPending}>{create.isPending ? "Starting…" : "Start rotation"}</Button>}>
        <div className="space-y-4"><Field label="Credential"><select className={formControl} value={credentialId} onChange={(event) => setCredentialId(event.target.value)}>{availableCredentials.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></Field><Field label="Urgency"><select className={formControl} value={urgency} onChange={(event) => setUrgency(event.target.value as typeof urgency)}><option value="routine">Routine</option><option value="urgent">Urgent</option><option value="emergency">Emergency</option></select></Field><Field label="Reason"><textarea className={`${formControl} h-24 py-3`} value={reason} onChange={(event) => setReason(event.target.value)} /></Field>{create.error && <div role="alert" className="rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{create.error.message}</div>}</div>
      </Modal>
    </div>
  )
}
