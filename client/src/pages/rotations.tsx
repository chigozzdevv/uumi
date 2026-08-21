import { useEffect, useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { Check, ChevronRight, Circle, Clock3, LoaderCircle, Plus, RotateCw, TriangleAlert } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, SelectControl, formControl } from "../components/workspace"
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
  if (["completed", "compensated"].includes(run.status)) return "healthy" as const
  if (run.status === "pending") return "neutral" as const
  return "active" as const
}

function RunStatus({ run }: { run: RotationRun }) {
  const moving = run.status === "running" || run.status === "recovering"
  return <Badge variant={variant(run)} className="gap-1.5">
    {moving && <LoaderCircle className="size-3 animate-spin" aria-hidden="true" />}
    {titleCase(run.status)}
  </Badge>
}

export function RotationsPage({ activeRunId, onNavigateApproval }: { activeRunId?: string; onNavigateApproval: () => void }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState("all")
  const [selectedId, setSelectedId] = useState(activeRunId ?? "")
  const [tab, setTab] = useState<"progress" | "details">("progress")
  const [creating, setCreating] = useState(false)
  const [credentialId, setCredentialId] = useState("")
  const [reason, setReason] = useState("Routine credential rotation")
  const [urgency, setUrgency] = useState<"routine" | "urgent" | "emergency">("routine")
  const [runs, graph] = useQueries({ queries: [
    { queryKey: ["rotations"], queryFn: () => api.getRotations(), refetchInterval: 5_000 },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })
  const create = useMutation({ mutationFn: () => {
    const selectedCredential = graph.data!.credentials.find((item) => item.id === credentialId)!
    return api.startRotation({ credential_id: selectedCredential.id, control_version: selectedCredential.control_version, reason: reason.trim(), urgency })
  }, onSuccess: async (run) => {
    await queryClient.invalidateQueries({ queryKey: ["rotations"] })
    setSelectedId(run.id)
    setCreating(false)
  } })

  useEffect(() => { if (activeRunId) setSelectedId(activeRunId) }, [activeRunId])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (runs.data ?? []).filter((run) => {
      const credential = graph.data?.credentials.find((item) => item.id === run.credential_id)
      const matchesSearch = !term || `${credential?.display_name ?? ""} ${run.stage} ${run.status} ${run.trigger.source}`.toLowerCase().includes(term)
      if (!matchesSearch) return false
      if (filter === "active") return ["pending", "running", "paused", "recovering"].includes(run.status)
      if (filter === "scheduled") return run.trigger.source === "scheduler" && run.status === "pending"
      if (filter === "completed") return ["completed", "compensated"].includes(run.status)
      if (filter === "failed") return ["failed", "cleanup-required"].includes(run.status)
      return true
    })
  }, [filter, graph.data?.credentials, runs.data, search])

  if ([runs, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [runs, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selected = runs.data!.find((run) => run.id === selectedId)
  const availableCredentials = graph.data!.credentials.filter((item) => !runs.data!.some((run) => run.credential_id === item.id && !["completed", "compensated", "failed", "cleanup-required"].includes(run.status)))

  function openCreate() {
    setCredentialId(availableCredentials[0]?.id ?? "")
    setReason("Routine credential rotation")
    setUrgency("routine")
    create.reset()
    setCreating(true)
  }

  if (selected) {
    const credential = graph.data!.credentials.find((item) => item.id === selected.credential_id)
    const stageIndex = stages.findIndex((stage) => stage.id === selected.stage)
    return <div className="page">
      <PageHeader eyebrow="Operations / Rotations" title={credential?.display_name ?? "Rotation"} titlePrefix={<Provider value={credential?.provider ?? "firekey"} label={false} />} onBack={() => setSelectedId("")} actions={selected.status === "paused" && selected.stage === "approval" ? <Button onClick={onNavigateApproval}>Review approval <Clock3 className="size-3.5" /></Button> : undefined} />
      <div className="mb-5 flex items-center gap-2"><RunStatus run={selected} /><Badge variant={selected.trigger.urgency === "emergency" ? "danger" : "neutral"}>{selected.trigger.urgency}</Badge></div>
      <DetailTabs items={[{ id: "progress", label: "Progress" }, { id: "details", label: "Details" }]} value={tab} onChange={setTab} />
      <DetailCard>
        {tab === "progress" && <div className="max-w-[620px]">{stages.map((stage, index) => {
            const completed = index < stageIndex || selected.status === "completed"
            const current = index === stageIndex && selected.status !== "completed"
            const failed = current && ["failed", "cleanup-required"].includes(selected.status)
            return <div key={stage.id} className="relative flex gap-4 pb-5 last:pb-0">
              {index < stages.length - 1 && <span className={`absolute left-[11px] top-6 h-[calc(100%-8px)] w-px ${completed ? "bg-[var(--green)]" : "bg-[var(--border)]"}`} />}
              <span className={`relative z-10 grid size-[23px] shrink-0 place-items-center rounded-full border ${completed ? "border-[var(--green)] bg-[var(--green)] text-white" : failed ? "border-[var(--red)] bg-[var(--red-soft)] text-[var(--red)]" : current ? "border-[var(--ink-soft)] bg-white text-[var(--ink)]" : "border-[var(--border)] bg-white text-[var(--ink-muted)]"}`}>{completed ? <Check className="size-3" /> : failed ? <TriangleAlert className="size-3" /> : current ? <RotateCw className={`size-3 ${selected.status === "running" ? "animate-spin" : ""}`} /> : <Circle className="size-2" />}</span>
              <div className="pt-0.5"><div className={`text-[11px] font-semibold ${current ? "text-[var(--ink)]" : completed ? "text-[var(--ink-soft)]" : "text-[var(--ink-muted)]"}`}>{stage.label}</div>{failed && selected.failure && <div className="mt-1 text-[9px] text-[var(--red)]">{selected.failure.message}</div>}</div>
            </div>
          })}</div>}
        {tab === "details" && <DetailList><Detail label="Stage">{titleCase(selected.stage)}</Detail><Detail label="Source">{titleCase(selected.trigger.source)}</Detail><Detail label="Reason">{selected.trigger.reason}</Detail><Detail label="Started">{formatDate(selected.trigger.received_at, true)}</Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList>}
      </DetailCard>
    </div>
  }

  return <div className="page">
    <PageHeader eyebrow="Operations" title="Rotations" actions={<Button onClick={openCreate} disabled={!availableCredentials.length}><Plus className="size-3.5" /> Start rotation</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search rotations" onClear={() => { setSearch(""); setFilter("all") }} filters={[{ label: "Status", value: filter, defaultValue: "all", onChange: (event) => setFilter(event.target.value), children: <><option value="all">All statuses</option><option value="active">Active</option><option value="scheduled">Scheduled</option><option value="completed">Completed</option><option value="failed">Failed</option></> }]} />
    <Table>
      <TableHeader><TableRow><TableHead>Credential</TableHead><TableHead>Stage</TableHead><TableHead>Status</TableHead><TableHead>Trigger</TableHead><TableHead>Updated</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody>{filtered.map((run) => {
        const credential = graph.data!.credentials.find((item) => item.id === run.credential_id)
        const needsApproval = run.status === "paused" && run.stage === "approval"
        return <TableRow key={run.id}>
          <TableCell><button className="focus-ring flex items-center gap-3 rounded-lg text-left font-medium hover:underline" onClick={() => { setSelectedId(run.id); setTab("progress") }}><Provider value={credential?.provider ?? "firekey"} label={false} />{credential?.display_name ?? "Credential"}</button></TableCell>
          <TableCell>{titleCase(run.stage)}</TableCell>
          <TableCell><RunStatus run={run} /></TableCell>
          <TableCell className="text-[var(--ink-soft)]">{titleCase(run.trigger.source)}</TableCell>
          <TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(run.updated_at)}</TableCell>
          <TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={() => { if (needsApproval) onNavigateApproval(); else { setSelectedId(run.id); setTab("progress") } }}>{needsApproval ? "Review approval" : "View details"} <ChevronRight className="size-3.5" /></Button></div></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
    <Modal isOpen={creating} onClose={() => setCreating(false)} title="Start rotation" actions={<Button onClick={() => create.mutate()} disabled={!credentialId || !reason.trim() || create.isPending}>{create.isPending ? "Starting…" : "Start rotation"}</Button>}>
      <div className="space-y-4"><Field label="Credential"><SelectControl value={credentialId} onChange={(event) => setCredentialId(event.target.value)}>{availableCredentials.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</SelectControl></Field><Field label="Urgency"><SelectControl value={urgency} onChange={(event) => setUrgency(event.target.value as typeof urgency)}><option value="routine">Routine</option><option value="urgent">Urgent</option><option value="emergency">Emergency</option></SelectControl></Field><Field label="Reason"><textarea className={`${formControl} h-24 py-3`} value={reason} onChange={(event) => setReason(event.target.value)} /></Field>{create.error && <div role="alert" className="rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{create.error.message}</div>}</div>
    </Modal>
  </div>
}
