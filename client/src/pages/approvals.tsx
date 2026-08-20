import { useEffect, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { Check, Clock3, FileSearch, ShieldCheck, X } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Failure, Loading } from "../components/state"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import type { Approval } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

export function ApprovalsPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState("")
  const [notice, setNotice] = useState("")
  const [approvals, runs, graph] = useQueries({ queries: [
    { queryKey: ["approvals"], queryFn: () => api.getApprovals() },
    { queryKey: ["rotations"], queryFn: () => api.getRotations() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
  ] })

  const decide = useMutation({
    mutationFn: ({ approval, decision }: { approval: Approval; decision: "approved" | "rejected" | "more-evidence" | "extend-observation" }) => api.decideApproval(approval.id, approval.revision, decision),
    onSuccess: (result) => {
      setNotice(`Decision recorded: ${titleCase(result.decision)}.`)
      queryClient.invalidateQueries({ queryKey: ["approvals"] })
      queryClient.invalidateQueries({ queryKey: ["overview"] })
    },
  })

  useEffect(() => {
    const first = approvals.data?.find((approval) => approval.decision === "pending")
    if (!selectedId && first) setSelectedId(first.id)
  }, [approvals.data, selectedId])

  if ([approvals, runs, graph].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [approvals, runs, graph].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selected = approvals.data!.find((approval) => approval.id === selectedId) ?? approvals.data![0]
  const run = runs.data!.find((item) => item.id === selected?.run_id)
  const credential = graph.data!.credentials.find((item) => item.id === run?.credential_id)
  const consumers = graph.data!.services.filter((item) => credential?.consumer_ids.includes(item.id))

  return (
    <div className="page">
      <PageHeader eyebrow="Operations" title="Approvals" />
      {notice && <button className="mb-5 flex w-full items-center gap-3 rounded-[14px] border border-[#bad8c9] bg-[var(--green-soft)] px-4 py-3 text-left text-[11px] text-[var(--green)]" onClick={() => setNotice("")}><Check className="size-3.5" />{notice}<span className="ml-auto">Dismiss</span></button>}
      {decide.error && <div className="mb-5 rounded-[14px] border border-[#dfb8bd] bg-[var(--red-soft)] px-4 py-3 text-[11px] text-[var(--red)]">{decide.error.message}</div>}

      <div className="grid gap-5 xl:grid-cols-[260px_1fr]">
        <div className="space-y-2">{approvals.data!.map((approval) => {
          const linkedRun = runs.data!.find((item) => item.id === approval.run_id)
          const item = graph.data!.credentials.find((entry) => entry.id === linkedRun?.credential_id)
          return <button key={approval.id} className={`focus-ring w-full rounded-[15px] border p-4 text-left transition ${selected?.id === approval.id ? "border-[var(--ink)] bg-white" : "border-[var(--border-soft)] bg-white/40 hover:bg-white/70"}`} onClick={() => setSelectedId(approval.id)}><div className="flex items-center justify-between gap-2"><div className="truncate text-[11px] font-semibold">{titleCase(approval.action_id.replace("action_", ""))}</div><Badge variant={approval.decision === "pending" ? "warning" : approval.decision === "approved" ? "healthy" : "neutral"}>{titleCase(approval.decision)}</Badge></div><div className="mt-2 truncate text-[10px] text-[var(--ink-soft)]">{item?.display_name ?? "Credential"}</div><div className="mt-3 flex items-center gap-1.5 text-[9px] text-[var(--ink-muted)]"><Clock3 className="size-3" /> Expires {formatDate(approval.expires_at, true)}</div></button>
        })}</div>

        {selected && <section className="panel overflow-hidden">
          <header className="border-b border-[var(--border)] p-6"><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold tracking-[-0.035em]">{titleCase(selected.action_id.replace("action_", ""))}</h2><Badge variant={selected.decision === "pending" ? "warning" : selected.decision === "approved" ? "healthy" : "neutral"}>{titleCase(selected.decision)}</Badge></div></header>
          <div className="grid 2xl:grid-cols-[1fr_290px]">
            <div className="p-6">
              <Section title="Evidence summary">
                <div className="grid gap-px overflow-hidden rounded-[14px] border border-[var(--border)] bg-[var(--border)] sm:grid-cols-2">
                  <Evidence label="Consumers migrated" value={`${consumers.length} of ${consumers.length}`} state="pass" />
                  <Evidence label="Replacement credential" value="Ready" state="pass" />
                  <Evidence label="Functional verification" value={run && ["approval", "revoke", "complete"].includes(run.stage) ? "Passed" : "Pending"} state={run && ["approval", "revoke", "complete"].includes(run.stage) ? "pass" : "wait"} />
                  <Evidence label="Authentication errors" value="0 observed" state="pass" />
                  <Evidence label="Old generation use" value="None during window" state="pass" />
                  <Evidence label="Rollback" value="Previous generation preserved" state="pass" />
                </div>
              </Section>
              <Section title="Action"><DetailList><Detail label="Credential">{credential?.display_name ?? "Credential"}</Detail><Detail label="Requested">{formatDate(selected.created_at, true)}</Detail><Detail label="Expires">{formatDate(selected.expires_at, true)}</Detail></DetailList></Section>
            </div>
            <aside className="border-t border-[var(--border)] bg-white/35 p-6 2xl:border-l 2xl:border-t-0">
              {selected.decision === "pending" ? <div className="space-y-2"><Button className="w-full" disabled={decide.isPending} onClick={() => decide.mutate({ approval: selected, decision: "approved" })}><ShieldCheck className="size-3.5" /> Approve action</Button><Button variant="secondary" className="w-full" disabled={decide.isPending} onClick={() => decide.mutate({ approval: selected, decision: "more-evidence" })}><FileSearch className="size-3.5" /> Request evidence</Button><Button variant="danger" className="w-full" disabled={decide.isPending} onClick={() => decide.mutate({ approval: selected, decision: "rejected" })}><X className="size-3.5" /> Reject</Button></div> : <div className="rounded-xl bg-[var(--surface-soft)] p-4 text-[10px] text-[var(--ink-soft)]"><span className="font-semibold text-[var(--ink)]">Decision recorded</span>{selected.decided_at ? ` · ${formatDate(selected.decided_at, true)}` : ""}</div>}
            </aside>
          </div>
        </section>}
      </div>
    </div>
  )
}

function Evidence({ label, value, state }: { label: string; value: string; state: "pass" | "wait" }) {
  return <div className="flex items-start gap-3 bg-white/75 p-4"><span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full ${state === "pass" ? "bg-[var(--green-soft)] text-[var(--green)]" : "bg-[var(--amber-soft)] text-[var(--amber)]"}`}>{state === "pass" ? <Check className="size-3" /> : <Clock3 className="size-3" />}</span><div><div className="data-label">{label}</div><div className="mt-1.5 text-[10px] font-semibold">{value}</div></div></div>
}
