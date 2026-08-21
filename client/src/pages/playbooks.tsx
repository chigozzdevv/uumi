import { useMemo, useState } from "react"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { BookOpenText, ChevronRight, Plus } from "lucide-react"
import { Detail, DetailCard, DetailList, DetailTabs, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { ManageResourceModal } from "../components/manage"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, FormGrid, SelectControl, SetupPage, formControl } from "../components/workspace"
import type { Playbook } from "../types"
import { api, type CreatePlaybookInput, type PlaybookDefinition, type PlaybookDetail } from "../lib/api"
import { formatDate, providerName } from "../lib/format"

const steps = ["Source", "Procedure", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

function playbookStatus(playbook: Playbook) {
  return playbook.active_version_id && playbook.active_version_id === playbook.latest_version_id ? "published" : "draft"
}

export function PlaybooksPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [platform, setPlatform] = useState("all")
  const [status, setStatus] = useState("all")
  const [selected, setSelected] = useState<Playbook | null>(null)
  const [tab, setTab] = useState<"overview" | "procedure" | "connections">("overview")
  const [creating, setCreating] = useState(false)
  const [versioning, setVersioning] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState("")
  const [playbooks, connections] = useQueries({ queries: [
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
  ] })
  const detail = useQuery({ queryKey: ["playbooks", selected?.id], queryFn: () => api.getPlaybook(selected!.id), enabled: Boolean(selected) })
  const currentSelected = detail.data?.playbook ?? selected
  const latestVersion = detail.data?.latest_version ?? detail.data?.active_version
  const renamePlaybook = useMutation({
    mutationFn: () => api.renamePlaybook(currentSelected!.id, currentSelected!.revision, editName.trim()),
    onSuccess: async (playbook) => {
      queryClient.setQueryData(["playbooks", playbook.id], (current: PlaybookDetail | undefined) => current ? { ...current, playbook } : { playbook, active_version: null, latest_version: null })
      setSelected(playbook)
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    },
  })
  const archivePlaybook = useMutation({
    mutationFn: () => api.archivePlaybook(currentSelected!.id, currentSelected!.revision),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ["playbooks", currentSelected!.id] })
      setEditing(false)
      setSelected(null)
      await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    },
  })
  const publishPlaybook = useMutation({
    mutationFn: () => api.publishPlaybook(currentSelected!.id, latestVersion!.id),
    onSuccess: async () => {
      const refreshed = await api.getPlaybook(currentSelected!.id)
      queryClient.setQueryData(["playbooks", currentSelected!.id], refreshed)
      setSelected(refreshed.playbook)
      await queryClient.invalidateQueries({ queryKey: ["playbooks"] })
    },
  })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (playbooks.data ?? []).filter((playbook) => {
      return (platform === "all" || playbook.platform === platform) && (status === "all" || playbookStatus(playbook) === status) && (!term || `${playbook.name} ${playbook.platform}`.toLowerCase().includes(term))
    })
  }, [platform, playbooks.data, search, status])
  if ([playbooks, connections].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [playbooks, connections].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const attached = (playbook: Playbook) => connections.data!.filter((connection) => connection.playbook_id === playbook.id && connection.playbook_version_id === playbook.active_version_id)

  if (creating || (versioning && detail.data)) return <PlaybookSetup existing={versioning ? detail.data : undefined} onClose={() => { setCreating(false); setVersioning(false) }} onCreated={async (playbook) => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["playbooks"] }), queryClient.invalidateQueries({ queryKey: ["playbooks", playbook.id] })]); setSelected(playbook); setTab("procedure"); setCreating(false); setVersioning(false) }} />

  if (currentSelected) return <div className="page">
    <PageHeader eyebrow="Management / Playbooks" title={currentSelected.name} onBack={() => setSelected(null)} actions={<>{latestVersion?.state === "draft" ? <Button onClick={() => publishPlaybook.mutate()} disabled={publishPlaybook.isPending}>{publishPlaybook.isPending ? "Publishing…" : "Publish"}</Button> : <Button onClick={() => setVersioning(true)}>New version</Button>}<Button variant="secondary" onClick={() => { setEditName(currentSelected.name); renamePlaybook.reset(); archivePlaybook.reset(); setEditing(true) }}>Edit</Button></>} />
    {publishPlaybook.error && <div role="alert" className="mb-4 text-[10px] text-[var(--red)]">{publishPlaybook.error.message}</div>}
    <DetailTabs items={[{ id: "overview", label: "Overview" }, { id: "procedure", label: "Procedure" }, { id: "connections", label: "Connections" }]} value={tab} onChange={setTab} />
    <DetailCard>
      {tab === "overview" && <DetailList><Detail label="Platform"><Provider value={currentSelected.platform} /></Detail><Detail label="Version">{currentSelected.latest_version}</Detail><Detail label="Status"><Badge variant={latestVersion?.state === "published" ? "healthy" : "warning"}>{latestVersion?.state === "published" ? "Published" : "Draft"}</Badge></Detail><Detail label="Actions">{latestVersion?.definition.steps.length ?? "None"}</Detail><Detail label="Updated">{formatDate(currentSelected.updated_at, true)}</Detail></DetailList>}
      {tab === "procedure" && <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{latestVersion?.definition.steps.map((step, index) => <div key={step.id} className="grid gap-2 py-4 sm:grid-cols-[32px_1fr_auto] sm:items-center"><span className="text-[10px] font-semibold text-[var(--ink-muted)]">{index + 1}</span><span className="text-[11px] font-medium">{step.objective}</span><span className="text-[9px] text-[var(--ink-muted)]">{step.stage}</span></div>)}{!latestVersion && <div className="py-5 text-[10px] text-[var(--ink-muted)]">No procedure.</div>}</div>}
      {tab === "connections" && <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{attached(currentSelected).map((connection) => <div key={connection.id} className="py-4 text-[11px] font-semibold">{connection.display_name}</div>)}{attached(currentSelected).length === 0 && <div className="py-5 text-[10px] text-[var(--ink-muted)]">Not attached to a browser connection.</div>}</div>}
    </DetailCard>
    <ManageResourceModal isOpen={editing} onClose={() => setEditing(false)} title="Edit playbook" resourceLabel="playbook" onSave={() => renamePlaybook.mutate()} onDelete={() => archivePlaybook.mutate()} dependencies={[
      { label: "Connections", items: attached(currentSelected).map((connection) => connection.display_name) },
    ]} saveDisabled={!editName.trim() || editName.trim() === currentSelected.name} saving={renamePlaybook.isPending} deleting={archivePlaybook.isPending} error={(renamePlaybook.error ?? archivePlaybook.error)?.message}>
      <Field label="Playbook name"><input className={formControl} value={editName} onChange={(event) => setEditName(event.target.value)} /></Field>
    </ManageResourceModal>
  </div>

  return <div className="page">
    <PageHeader eyebrow="Management" title="Playbooks" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add playbook</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search playbooks or platforms" onClear={() => { setSearch(""); setPlatform("all"); setStatus("all") }} filters={[{ label: "Platform", value: platform, defaultValue: "all", onChange: (event) => setPlatform(event.target.value), children: <><option value="all">All platforms</option>{[...new Set(playbooks.data!.map((item) => item.platform))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</> }, { label: "Status", value: status, defaultValue: "all", onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="published">Published</option><option value="draft">Draft</option></> }]} />
    <Table><TableHeader><TableRow><TableHead>Playbook</TableHead><TableHead>Platform</TableHead><TableHead>Connections</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="pr-0 text-right">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((playbook) => {
      const open = () => { setSelected(playbook); setTab("overview") }
      const published = playbookStatus(playbook) === "published"
      return <TableRow key={playbook.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={open}><Marker icon={BookOpenText} />{playbook.name}</button></TableCell><TableCell><Provider value={playbook.platform} /></TableCell><TableCell>{attached(playbook).length}</TableCell><TableCell>{playbook.latest_version}</TableCell><TableCell><Badge variant={published ? "healthy" : "warning"}>{published ? "Published" : "Draft"}</Badge></TableCell><TableCell className="pr-0"><div className="flex justify-end"><Button className="pr-1" variant="ghost" size="sm" onClick={open}>View details <ChevronRight className="size-3.5" /></Button></div></TableCell></TableRow>
    })}</TableBody></Table>
  </div>
}

export function PlaybookSetup({ onClose, onCreated, existing, initialPlatform = "" }: { onClose: () => void; onCreated: (playbook: Playbook) => Promise<void>; existing?: PlaybookDetail; initialPlatform?: string }) {
  const [step, setStep] = useState(0)
  const [sourceType, setSourceType] = useState<"text" | "link" | "video">("text")
  const [source, setSource] = useState("")
  const [sourceNotes, setSourceNotes] = useState("")
  const [name, setName] = useState(existing?.playbook.name ?? "")
  const [platform, setPlatform] = useState(existing?.playbook.platform ?? initialPlatform)
  const existingVersion = existing?.latest_version ?? existing?.active_version
  const [domains, setDomains] = useState(existingVersion?.definition.allowed_domains.join(", ") ?? "")
  const [loginUrl, setLoginUrl] = useState(existingVersion?.definition.login_url_pattern ?? "")
  const [actions, setActions] = useState(existingVersion?.definition.steps.map((item) => item.objective).join("\n") ?? "Open credential settings\nCreate replacement credential and capture it\nFind the previous credential\nRevoke the previous credential")
  const mutation = useMutation({ mutationFn: (input: CreatePlaybookInput) => api.createPlaybook(input) })

  const actionList = actions.split("\n").map((item) => item.trim()).filter(Boolean)
  function canContinue() {
    if (step === 0) return Boolean(source.trim() && (sourceType === "text" || sourceNotes.trim()))
    if (step === 1) return Boolean(name.trim() && platform.trim() && domains.trim() && loginUrl.trim() && actionList.length >= 2)
    return true
  }

  async function submit() {
    const playbookId = existing?.playbook.id ?? identifier("playbook")
    const versionId = identifier("playbook_version")
    const domain = domains.split(",").map((item) => item.trim()).filter(Boolean)[0]
    const checkpoint = { url_pattern: `https://${domain.replace(/^\*\./, "*")}/**`, required_text: [], forbidden_text: [] }
    const selector = (value: string) => ({ kind: "test-id" as const, value, name: null, exact: true })
    const creationIndex = Math.max(0, actionList.findIndex((objective, index) => /create|generate|capture/i.test(objective) && index < actionList.length - 1))
    const revocationIndex = actionList.findIndex((objective) => /revoke|disable|delete/i.test(objective))
    const effectiveRevocationIndex = revocationIndex >= 0 ? revocationIndex : actionList.length - 1
    const procedureSteps: PlaybookDefinition["steps"] = actionList.map((objective, index) => {
      const capture = index === creationIndex
      const revoke = index === effectiveRevocationIndex
      const revokeStage = index > creationIndex
      const target = selector(capture ? "generated-credential" : revoke ? "revoke-credential" : `action-${index + 1}`)
      return {
        id: `action_${index + 1}`,
        stage: revokeStage ? "revoke" : "create",
        effect: capture ? "create-credential" : revoke ? "revoke-credential" : "none",
        tool: capture ? "browser.secure-capture" : "browser.click",
        operation: capture ? "capture" : revoke ? "revoke" : "click",
        objective,
        parameters: {},
        protected: false,
        evidence_checks: [capture ? "credential-captured" : revoke ? "credential-revoked" : "checkpoint-confirmed"],
        selectors: [target],
        checkpoint,
        secure_field: capture ? { name: "credential", selector: target, provider_id_selector: selector("credential-id") } : null,
        outputs: [],
        timeout_seconds: 30,
        retry_limit: 1,
      }
    })
    const created = await mutation.mutateAsync({
      playbook_id: playbookId,
      version_id: versionId,
      source: {
        id: identifier(`source_${sourceType}`),
        kind: sourceType,
        content: sourceType === "text" ? source.trim() : sourceNotes.trim(),
        resource_url: sourceType === "text" ? undefined : source.trim(),
      },
      definition: { name: name.trim(), platform: slug(platform), allowed_domains: domains.split(",").map((item) => item.trim()).filter(Boolean), login_url_pattern: loginUrl.trim(), steps: procedureSteps },
    })
    await onCreated(created)
  }

  const primary = step < 2 ? "Continue" : "Build draft"
  return <SetupPage eyebrow="Management / Playbooks" title={existing ? "New playbook version" : "Add playbook"} steps={steps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={mutation.error?.message} primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>{primary}</Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Building…" : primary}</Button>}>
    {step === 0 && <FormGrid><Field label="Source type"><SelectControl value={sourceType} onChange={(event) => setSourceType(event.target.value as typeof sourceType)}><option value="text">Text instructions</option><option value="link">Resource link</option><option value="video">Video walkthrough</option></SelectControl></Field><Field label={sourceType === "text" ? "Instructions" : sourceType === "link" ? "Resource URL" : "Video URL"} wide>{sourceType === "text" ? <textarea className={`${formControl} h-36 py-3`} value={source} onChange={(event) => setSource(event.target.value)} placeholder="Describe how to create, capture, and revoke the credential." /> : <input className={formControl} type="url" value={source} onChange={(event) => setSource(event.target.value)} placeholder="https://…" />}</Field>{sourceType !== "text" && <Field label="Sanitised notes or transcript" wide><textarea className={`${formControl} h-28 py-3`} value={sourceNotes} onChange={(event) => setSourceNotes(event.target.value)} placeholder="Paste the relevant non-secret procedure." /></Field>}</FormGrid>}
    {step === 1 && <FormGrid><Field label="Playbook name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production credential rotation" /></Field><Field label="Platform"><input className={formControl} value={platform} onChange={(event) => setPlatform(event.target.value)} placeholder="platform-id" /></Field><Field label="Allowed domains"><input className={formControl} value={domains} onChange={(event) => setDomains(event.target.value)} placeholder="*.platform.example" /></Field><Field label="Login URL pattern"><input className={formControl} value={loginUrl} onChange={(event) => setLoginUrl(event.target.value)} placeholder="https://platform.example/login*" /></Field><Field label="Ordered actions" wide><textarea className={`${formControl} h-40 py-3`} value={actions} onChange={(event) => setActions(event.target.value)} /></Field></FormGrid>}
    {step === 2 && <div className="grid gap-5 lg:grid-cols-2"><Section title="Version"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={slug(platform)} /></Detail><Detail label="Source">{sourceType}</Detail></DetailList></Section><Section title="Procedure"><div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{actionList.map((action, index) => <div key={`${index}-${action}`} className="grid grid-cols-[24px_1fr] gap-2 py-3 text-[10px]"><span className="text-[var(--ink-muted)]">{index + 1}</span><span>{action}</span></div>)}</div></Section></div>}
  </SetupPage>
}
