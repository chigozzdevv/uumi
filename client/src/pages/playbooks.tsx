import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, ArrowRight, BookOpenText, Plus } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Journey } from "../components/journey"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Modal } from "../components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import type { Playbook } from "../types"
import { api, type CreatePlaybookInput, type PlaybookDefinition } from "../lib/api"
import { formatDate, providerName } from "../lib/format"

const field = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none"
const steps = ["Source", "Procedure", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
}

function Label({ title, children }: { title: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[10px] font-medium text-[var(--ink-soft)]">{title}</span>{children}</label>
}

export function PlaybooksPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [platform, setPlatform] = useState("all")
  const [status, setStatus] = useState("all")
  const [selected, setSelected] = useState<Playbook | null>(null)
  const [creating, setCreating] = useState(false)
  const [playbooks, connections] = useQueries({ queries: [
    { queryKey: ["playbooks"], queryFn: () => api.getPlaybooks() },
    { queryKey: ["connections"], queryFn: () => api.getConnections() },
  ] })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (playbooks.data ?? []).filter((playbook) => {
      const playbookStatus = playbook.active_version_id ? "published" : "draft"
      return (platform === "all" || playbook.platform === platform) && (status === "all" || playbookStatus === status) && (!term || `${playbook.name} ${playbook.platform}`.toLowerCase().includes(term))
    })
  }, [platform, playbooks.data, search, status])
  if ([playbooks, connections].some((query) => query.isLoading)) return <div className="page"><Loading /></div>
  const error = [playbooks, connections].find((query) => query.error)?.error
  if (error) return <div className="page"><Failure error={error} /></div>

  const attached = (playbook: Playbook) => connections.data!.filter((connection) => connection.playbook_id === playbook.id && connection.playbook_version_id === playbook.active_version_id)

  return (
    <div className="page">
      <PageHeader section="Governance · Playbooks" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add Playbook</Button>} />
      <Toolbar value={search} onChange={setSearch} placeholder="Search Playbooks or platforms" filters={[{ label: "Platform", value: platform, onChange: (event) => setPlatform(event.target.value), children: <><option value="all">All platforms</option>{[...new Set(playbooks.data!.map((item) => item.platform))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</> }, { label: "Status", value: status, onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="published">Published</option><option value="draft">Draft</option></> }]} />
      <div><Table><TableHeader><TableRow><TableHead>Playbook</TableHead><TableHead>Platform</TableHead><TableHead>Connections</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Updated</TableHead></TableRow></TableHeader><TableBody>{rows.map((playbook) => <TableRow key={playbook.id} className="cursor-pointer" onClick={() => setSelected(playbook)}><TableCell><div className="flex items-center gap-3"><Marker icon={BookOpenText} tone="neutral" /><span className="font-medium">{playbook.name}</span></div></TableCell><TableCell><Provider value={playbook.platform} /></TableCell><TableCell>{attached(playbook).length}</TableCell><TableCell>{playbook.latest_version}</TableCell><TableCell><Badge variant={playbook.active_version_id ? "healthy" : "warning"}>{playbook.active_version_id ? "Published" : "Draft"}</Badge></TableCell><TableCell className="text-right text-[10px] text-[var(--ink-soft)]">{formatDate(playbook.updated_at)}</TableCell></TableRow>)}</TableBody></Table></div>
      <Modal isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.name ?? "Playbook"}>
        {selected && <><Section title="Procedure"><DetailList><Detail label="Platform"><Provider value={selected.platform} /></Detail><Detail label="Version">{selected.latest_version}</Detail><Detail label="Status"><Badge variant={selected.active_version_id ? "healthy" : "warning"}>{selected.active_version_id ? "Published" : "Draft"}</Badge></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section><Section title="Browser connections"><div className="space-y-2">{attached(selected).map((connection) => <div key={connection.id} className="rounded-xl border border-[var(--border-soft)] bg-white/65 p-4 text-[11px] font-semibold">{connection.display_name}</div>)}{attached(selected).length === 0 && <div className="text-[10px] text-[var(--ink-muted)]">Not attached to a browser connection.</div>}</div></Section></>}
      </Modal>
      <PlaybookSetup isOpen={creating} onClose={() => setCreating(false)} onCreated={async () => { await queryClient.invalidateQueries({ queryKey: ["playbooks"] }) }} />
    </div>
  )
}

function PlaybookSetup({ isOpen, onClose, onCreated }: { isOpen: boolean; onClose: () => void; onCreated: () => Promise<void> }) {
  const [step, setStep] = useState(0)
  const [sourceType, setSourceType] = useState<"text" | "link" | "video">("text")
  const [source, setSource] = useState("")
  const [sourceNotes, setSourceNotes] = useState("")
  const [name, setName] = useState("")
  const [platform, setPlatform] = useState("")
  const [domains, setDomains] = useState("")
  const [loginUrl, setLoginUrl] = useState("")
  const [actions, setActions] = useState("Open credential settings\nCreate replacement credential and capture it\nFind the previous credential\nRevoke the previous credential")
  const mutation = useMutation({ mutationFn: (input: CreatePlaybookInput) => api.createPlaybook(input), onSuccess: onCreated })

  useEffect(() => {
    if (!isOpen) return
    setStep(0); setSourceType("text"); setSource(""); setSourceNotes(""); setName(""); setPlatform(""); setDomains(""); setLoginUrl(""); setActions("Open credential settings\nCreate replacement credential and capture it\nFind the previous credential\nRevoke the previous credential"); mutation.reset()
  }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps

  const actionList = actions.split("\n").map((item) => item.trim()).filter(Boolean)
  function canContinue() {
    if (step === 0) return Boolean(source.trim() && (sourceType === "text" || sourceNotes.trim()))
    if (step === 1) return Boolean(name.trim() && platform.trim() && domains.trim() && loginUrl.trim() && actionList.length >= 2)
    return true
  }

  async function submit() {
    const playbookId = identifier("playbook")
    const versionId = identifier("playbook_version")
    const domain = domains.split(",").map((item) => item.trim()).filter(Boolean)[0]
    const checkpoint = { url_pattern: `https://${domain.replace(/^\*\./, "*")}/**`, required_text: [], forbidden_text: [] }
    const selector = (value: string) => ({ kind: "test-id" as const, value, name: null, exact: true })
    const procedureSteps: PlaybookDefinition["steps"] = actionList.map((objective, index) => {
      const capture = /create|capture/i.test(objective) && index < actionList.length - 1
      const revoke = /revoke|disable|delete/i.test(objective) || index === actionList.length - 1
      const target = selector(capture ? "generated-credential" : revoke ? "revoke-credential" : `action-${index + 1}`)
      return {
        id: `action_${index + 1}`,
        stage: revoke ? "revoke" : "create",
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
    if (!procedureSteps.some((item) => item.secure_field)) procedureSteps[0] = { ...procedureSteps[0], tool: "browser.secure-capture", operation: "capture", secure_field: { name: "credential", selector: procedureSteps[0].selectors[0], provider_id_selector: selector("credential-id") } }
    if (!procedureSteps.some((item) => item.stage === "revoke")) procedureSteps[procedureSteps.length - 1] = { ...procedureSteps[procedureSteps.length - 1], stage: "revoke", tool: "browser.click", operation: "revoke", secure_field: null }
    await mutation.mutateAsync({
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
    onClose()
  }

  return <Modal isOpen={isOpen} onClose={onClose} title="Add Playbook" size="wide" footerStart={step > 0 ? <Button variant="ghost" onClick={() => setStep((value) => value - 1)}><ArrowLeft className="size-3.5" /> Back</Button> : undefined} actions={step < steps.length - 1 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>Continue <ArrowRight className="size-3.5" /></Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Publishing…" : "Publish Playbook"}</Button>}>
    <Journey steps={steps} current={step} />
    {step === 0 && <div className="grid gap-4 sm:grid-cols-2"><Label title="Source type"><select className={field} value={sourceType} onChange={(event) => setSourceType(event.target.value as typeof sourceType)}><option value="text">Text instructions</option><option value="link">Resource link</option><option value="video">Video walkthrough</option></select></Label><div className="sm:col-span-2"><Label title={sourceType === "text" ? "Instructions" : sourceType === "link" ? "Resource URL" : "Video URL"}>{sourceType === "text" ? <textarea className={`${field} h-32 py-3`} value={source} onChange={(event) => setSource(event.target.value)} placeholder="Describe how an operator creates and revokes the credential." /> : <input className={field} type="url" value={source} onChange={(event) => setSource(event.target.value)} placeholder="https://…" />}</Label></div>{sourceType !== "text" && <div className="sm:col-span-2"><Label title="Sanitised notes or transcript"><textarea className={`${field} h-28 py-3`} value={sourceNotes} onChange={(event) => setSourceNotes(event.target.value)} placeholder="Paste the relevant non-secret procedure from this resource." /></Label><div className="mt-2 text-[9px] text-[var(--ink-muted)]">The URL is retained only as provenance. The version is built from these sanitised notes.</div></div>}</div>}
    {step === 1 && <div className="grid gap-4 sm:grid-cols-2"><Label title="Playbook name"><input className={field} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production credential rotation" /></Label><Label title="Platform"><input className={field} value={platform} onChange={(event) => setPlatform(event.target.value)} placeholder="platform-id" /></Label><Label title="Allowed domains"><input className={field} value={domains} onChange={(event) => setDomains(event.target.value)} placeholder="*.platform.example" /></Label><Label title="Login URL pattern"><input className={field} value={loginUrl} onChange={(event) => setLoginUrl(event.target.value)} placeholder="https://platform.example/login*" /></Label><div className="sm:col-span-2"><Label title="Ordered actions"><textarea className={`${field} h-36 py-3`} value={actions} onChange={(event) => setActions(event.target.value)} /></Label><div className="mt-2 text-[9px] text-[var(--ink-muted)]">One action per line. Actions become deterministic steps inside this Playbook version.</div></div></div>}
    {step === 2 && <><Section title="Version"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={slug(platform)} /></Detail><Detail label="Source">{sourceType}</Detail><Detail label="Actions">{actionList.length}</Detail></DetailList></Section><Section title="Structural validation"><div className="space-y-2 text-[10px] text-[var(--ink-soft)]"><div className="rounded-xl bg-white/70 p-3">Allowed domains and login checkpoint declared</div><div className="rounded-xl bg-white/70 p-3">Secure capture kept outside the model and recording</div><div className="rounded-xl bg-white/70 p-3">Create and revoke actions present</div></div></Section></>}
    {mutation.error && <div role="alert" className="mt-5 rounded-xl bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{mutation.error.message}</div>}
  </Modal>
}
