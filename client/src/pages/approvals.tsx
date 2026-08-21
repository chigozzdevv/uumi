import { useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ChevronRight } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs } from "../components/detail"
import { PageHeader } from "../components/header"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Approval } from "../types"
import { api } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

function approvalName(approval: Approval) {
  if (approval.action_id.includes("revoke")) return "Revoke previous credential"
  if (approval.action_id.includes("takeover")) return "Continue browser authentication"
  return titleCase(approval.action_id.replace(/^action_/, ""))
}

function approvalStatus(decision: Approval["decision"]): { label: string; variant: "warning" | "healthy" | "danger" | "neutral" } {
  if (decision === "pending") return { label: "Action required", variant: "warning" }
  if (decision === "approved") return { label: "Approved", variant: "healthy" }
  if (decision === "rejected") return { label: "Rejected", variant: "danger" }
  if (decision === "more-evidence") return { label: "Evidence requested", variant: "neutral" }
  return { label: "Verification extended", variant: "neutral" }
}

export function ApprovalsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("pending")
  const [selectedId, setSelectedId] = useState("")
  const [detailTab, setDetailTab] = useState<"request" | "evidence">("request")
  const [notice, setNotice] = useState("")
  const [approvals, runs, graph, audits] = useQueries({ queries: [
    { queryKey: ["approvals"], queryFn: () => api.getApprovals() },
    { queryKey: ["rotations"], queryFn: () => api.getRotations() },
    { queryKey: ["graph"], queryFn: () => api.getGraph() },
    { queryKey: ["audits"], queryFn: () => api.getAudits() },
  ] })

  const decide = useMutation({
    mutationFn: ({ approval, decision }: { approval: Approval; decision: "approved" | "rejected" | "more-evidence" | "extend-observation" }) => api.decideApproval(approval.id, approval.revision, decision),
    onSuccess: async (result) => {
      queryClient.setQueryData<Approval[]>(["approvals"], (current) => current?.map((item) => item.id === result.id ? result : item))
      setNotice(`${approvalName(result)} · ${approvalStatus(result.decision).label}`)
      setSelectedId("")
      setDetailTab("request")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ])
    },
  })

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (approvals.data ?? []).filter((approval) => {
      const run = runs.data?.find((item) => item.id === approval.run_id)
      const credential = graph.data?.credentials.find((item) => item.id === run?.credential_id)
      const matchesStatus = status === "all" || approval.decision === status
      const haystack = `${approvalName(approval)} ${credential?.display_name ?? ""} ${run?.stage ?? ""}`.toLowerCase()
      return matchesStatus && (!term || haystack.includes(term))
    })
  }, [approvals.data, graph.data, runs.data, search, status])

  if ([approvals, runs, graph, audits].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [approvals, runs, graph, audits].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const selected = approvals.data!.find((approval) => approval.id === selectedId)
  const selectedRun = runs.data!.find((item) => item.id === selected?.run_id)
  const selectedCredential = graph.data!.credentials.find((item) => item.id === selectedRun?.credential_id)
  const selectedEvidenceEvents = audits.data!.filter((item) => item.run_id === selected?.run_id && item.evidence_ids.length).sort((left, right) => Date.parse(right.occurred_at) - Date.parse(left.occurred_at))
  const selectedEvidenceIds = [...new Set(selectedEvidenceEvents.flatMap((item) => item.evidence_ids))]

  if (selected) {
    const presentation = approvalStatus(selected.decision)
    return <div className="page">
      <PageHeader eyebrow="Operations / Approvals" title={approvalName(selected)} titlePrefix={selectedCredential ? <Provider value={selectedCredential.provider} label={false} /> : undefined} onBack={() => { setSelectedId(""); setDetailTab("request"); decide.reset() }} actions={selected.decision === "pending" ? <><Button variant="danger" disabled={decide.isPending} onClick={() => decide.mutate({ approval: selected, decision: "rejected" })}>Reject</Button><Button disabled={decide.isPending} onClick={() => decide.mutate({ approval: selected, decision: "approved" })}>Approve</Button></> : undefined} />
      <div className="mb-6"><Badge variant={presentation.variant}>{presentation.label}</Badge></div>
      {decide.error && <div role="alert" className="mb-6 rounded-xl border border-[var(--border)] bg-white px-4 py-3 text-[10px] text-[var(--red)]">{decide.error.message}</div>}
      <DetailTabs items={[{ id: "request", label: "Request" }, { id: "evidence", label: "Evidence" }]} value={detailTab} onChange={setDetailTab} />
      <DetailCard>
        {detailTab === "request" && <DetailList><Detail label="Credential">{selectedCredential?.display_name ?? "Credential"}</Detail><Detail label="Stage">{titleCase(selectedRun?.stage ?? "unknown")}</Detail><Detail label="Requested">{formatDate(selected.created_at, true)}</Detail><Detail label="Expires">{formatDate(selected.expires_at, true)}</Detail><Detail label="Requested by">{titleCase(selected.requested_by.replace(/^actor_/, ""))}</Detail>{selected.decided_at && <Detail label="Decided">{formatDate(selected.decided_at, true)}</Detail>}</DetailList>}
        {detailTab === "evidence" && <div className="space-y-6"><DetailList><Detail label="Records">{selectedEvidenceIds.length}</Detail><Detail label="Recorded checks">{selectedEvidenceEvents.length}</Detail><Detail label="Snapshot"><span className="mono-code">{selected.evidence_hash.slice(0, 12)}…</span></Detail><Detail label="Latest record">{selectedEvidenceEvents[0] ? formatDate(selectedEvidenceEvents[0].occurred_at, true) : "None"}</Detail></DetailList>{selectedEvidenceEvents.length ? <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{selectedEvidenceEvents.map((event) => <div key={event.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_1fr_auto] sm:items-center"><div><div className="text-[11px] font-semibold">{titleCase(event.kind)}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">{formatDate(event.occurred_at, true)}</div></div><div className="mono-code break-all text-[9px] text-[var(--ink-soft)]">{event.evidence_ids.join(", ")}</div><div className="text-[9px] text-[var(--ink-muted)]">#{event.sequence}</div></div>)}</div> : <div className="border-y border-[var(--border-soft)] py-5 text-[10px] text-[var(--ink-muted)]">No evidence records are linked to this run.</div>}</div>}
      </DetailCard>
    </div>
  }

  return <div className="page">
    <PageHeader eyebrow="Operations" title="Approvals" />
    {notice && <button className="mb-5 flex w-full items-center rounded-xl border border-[var(--border)] bg-white px-4 py-3 text-left text-[10px] font-medium text-[var(--ink-soft)]" onClick={() => setNotice("")}>{notice}<span className="ml-auto">Dismiss</span></button>}
    <Toolbar value={search} onChange={setSearch} placeholder="Search approvals or credentials" onClear={() => { setSearch(""); setStatus("pending") }} filters={[{ label: "Status", value: status, defaultValue: "pending", onChange: (event) => setStatus(event.target.value), children: <><option value="pending">Action required</option><option value="all">All approvals</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="more-evidence">Evidence requested</option><option value="extend-observation">Verification extended</option></> }]} />
    <Table>
      <TableHeader><TableRow><TableHead>Requested action</TableHead><TableHead>Credential</TableHead><TableHead>Stage</TableHead><TableHead>Requested</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader>
      <TableBody>{rows.map((approval) => {
        const run = runs.data!.find((item) => item.id === approval.run_id)
        const credential = graph.data!.credentials.find((item) => item.id === run?.credential_id)
        const presentation = approvalStatus(approval.decision)
        return <TableRow key={approval.id}>
          <TableCell><button className="focus-ring rounded-lg text-left font-medium hover:underline" onClick={() => { setSelectedId(approval.id); setDetailTab("request"); decide.reset() }}>{approvalName(approval)}</button></TableCell>
          <TableCell><div>{credential?.display_name ?? "Credential"}</div><div className="mt-1 text-[9px] text-[var(--ink-muted)]">Expires {formatDate(approval.expires_at)}</div></TableCell>
          <TableCell>{titleCase(run?.stage ?? "unknown")}</TableCell>
          <TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(approval.created_at)}</TableCell>
          <TableCell><Badge variant={presentation.variant}>{presentation.label}</Badge></TableCell>
          <TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={() => { setSelectedId(approval.id); setDetailTab("request"); decide.reset() }}>{approval.decision === "pending" ? "Review" : "View details"} <ChevronRight className="size-3.5" /></Button></div></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
  </div>
}
