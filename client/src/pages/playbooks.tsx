import { useMemo, useState } from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { BookOpenText, ChevronRight, Plus } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Provider } from "../components/provider"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, FormGrid, SetupPage, formControl } from "../components/workspace"
import type { Playbook } from "../types"
import { api, type CreatePlaybookInput, type PlaybookDefinition } from "../lib/api"
import { formatDate, providerName } from "../lib/format"

const steps = ["Source", "Procedure", "Review"]

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64)
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

  if (creating) return <PlaybookSetup onClose={() => setCreating(false)} onCreated={async () => { await queryClient.invalidateQueries({ queryKey: ["playbooks"] }); setCreating(false) }} />

  if (selected) return <div className="page">
    <PageHeader eyebrow="Playbooks" title={selected.name} description="A versioned browser procedure for performing credential actions on this platform." onBack={() => setSelected(null)} />
    <div className="grid gap-5 xl:grid-cols-2"><Section title="Procedure"><DetailList><Detail label="Platform"><Provider value={selected.platform} /></Detail><Detail label="Version">{selected.latest_version}</Detail><Detail label="Status"><Badge variant={selected.active_version_id ? "healthy" : "warning"}>{selected.active_version_id ? "Published" : "Draft"}</Badge></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail></DetailList></Section><Section title="Browser connections"><div className="space-y-2">{attached(selected).map((connection) => <div key={connection.id} className="rounded-xl border border-[var(--border-soft)] p-4 text-[11px] font-semibold">{connection.display_name}</div>)}{attached(selected).length === 0 && <div className="rounded-xl bg-[var(--surface-soft)] p-5 text-[10px] text-[var(--ink-soft)]">Not attached to a browser connection. Attach it from a matching connection.</div>}</div></Section></div>
  </div>

  return <div className="page">
    <PageHeader title="Playbooks" description="Turn text, links, or video walkthroughs into versioned browser action procedures." actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add playbook</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search playbooks or platforms" resultCount={rows.length} resultLabel="playbooks" onClear={() => { setSearch(""); setPlatform("all"); setStatus("all") }} filters={[{ label: "Platform", value: platform, defaultValue: "all", onChange: (event) => setPlatform(event.target.value), children: <><option value="all">All platforms</option>{[...new Set(playbooks.data!.map((item) => item.platform))].map((item) => <option key={item} value={item}>{providerName(item)}</option>)}</> }, { label: "Status", value: status, defaultValue: "all", onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="published">Published</option><option value="draft">Draft</option></> }]} />
    <Table><TableHeader><TableRow><TableHead>Playbook</TableHead><TableHead>Platform</TableHead><TableHead>Connections</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead className="w-36">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((playbook) => <TableRow key={playbook.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={() => setSelected(playbook)}><Marker icon={BookOpenText} />{playbook.name}</button></TableCell><TableCell><Provider value={playbook.platform} /></TableCell><TableCell>{attached(playbook).length}</TableCell><TableCell>{playbook.latest_version}</TableCell><TableCell><Badge variant={playbook.active_version_id ? "healthy" : "warning"}>{playbook.active_version_id ? "Published" : "Draft"}</Badge></TableCell><TableCell><Button variant="ghost" size="sm" onClick={() => setSelected(playbook)}>View details <ChevronRight className="size-3.5" /></Button></TableCell></TableRow>)}</TableBody></Table>
  </div>
}

function PlaybookSetup({ onClose, onCreated }: { onClose: () => void; onCreated: () => Promise<void> }) {
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

  const primary = ["Continue to procedure", "Review playbook", "Build and publish"][step]
  return <SetupPage eyebrow="Playbooks" title="Add playbook" description="Provide a browser walkthrough, refine its ordered actions, and publish a versioned procedure." steps={steps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={mutation.error?.message} primary={step < 2 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue()}>{primary}</Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Building…" : primary}</Button>}>
    {step === 0 && <FormGrid><Field label="Source type"><select className={formControl} value={sourceType} onChange={(event) => setSourceType(event.target.value as typeof sourceType)}><option value="text">Text instructions</option><option value="link">Resource link</option><option value="video">Video walkthrough</option></select></Field><Field label={sourceType === "text" ? "Instructions" : sourceType === "link" ? "Resource URL" : "Video URL"} wide>{sourceType === "text" ? <textarea className={`${formControl} h-36 py-3`} value={source} onChange={(event) => setSource(event.target.value)} placeholder="Describe how to create, capture, and revoke the credential." /> : <input className={formControl} type="url" value={source} onChange={(event) => setSource(event.target.value)} placeholder="https://…" />}</Field>{sourceType !== "text" && <Field label="Sanitised notes or transcript" hint="The URL remains provenance. The playbook is built from these non-secret instructions." wide><textarea className={`${formControl} h-28 py-3`} value={sourceNotes} onChange={(event) => setSourceNotes(event.target.value)} placeholder="Paste the relevant non-secret procedure." /></Field>}</FormGrid>}
    {step === 1 && <FormGrid><Field label="Playbook name"><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production credential rotation" /></Field><Field label="Platform"><input className={formControl} value={platform} onChange={(event) => setPlatform(event.target.value)} placeholder="platform-id" /></Field><Field label="Allowed domains"><input className={formControl} value={domains} onChange={(event) => setDomains(event.target.value)} placeholder="*.platform.example" /></Field><Field label="Login URL pattern"><input className={formControl} value={loginUrl} onChange={(event) => setLoginUrl(event.target.value)} placeholder="https://platform.example/login*" /></Field><Field label="Ordered actions" hint="One action per line. These become the steps inside this playbook version." wide><textarea className={`${formControl} h-40 py-3`} value={actions} onChange={(event) => setActions(event.target.value)} /></Field></FormGrid>}
    {step === 2 && <div className="grid gap-5 lg:grid-cols-2"><Section title="Version"><DetailList><Detail label="Name">{name}</Detail><Detail label="Platform"><Provider value={slug(platform)} /></Detail><Detail label="Source">{sourceType}</Detail><Detail label="Actions">{actionList.length}</Detail></DetailList></Section><Section title="Safety boundaries"><div className="space-y-2 text-[10px] text-[var(--ink-soft)]"><div className="rounded-xl bg-[var(--surface-soft)] p-3">Playbook Builder Agent will use the sanitised source evidence</div><div className="rounded-xl bg-[var(--surface-soft)] p-3">Secure capture remains outside the model and recording</div><div className="rounded-xl bg-[var(--surface-soft)] p-3">Schema, domain, create, and revoke checks run before publication</div></div></Section></div>}
  </SetupPage>
}
