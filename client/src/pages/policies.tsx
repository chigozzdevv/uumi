import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, Plus, ShieldCheck } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Marker } from "../components/marker"
import { Failure, Loading } from "../components/state"
import { Toolbar } from "../components/toolbar"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table"
import { Field, Fieldset, FormGrid, SetupPage, formControl } from "../components/workspace"
import type { Policy } from "../types"
import { api, type CreatePolicyInput, type PolicyDefinition } from "../lib/api"
import { formatDate, titleCase } from "../lib/format"

const steps = ["Basics", "Automation", "Safety", "Review"]
const triggers = ["schedule", "expiry", "drift", "verified-exposure"]
const allowedTools = ["provider.listCredentialMetadata", "provider.getCredentialStatus", "provider.createCredential", "provider.revokeCredential", "secretStore.getVersion", "secretStore.disableVersion", "secretStore.destroyVersion", "runtime.inspectSecretBindings", "runtime.deployCandidate", "runtime.shiftTraffic", "runtime.rollback", "telemetry.queryHealth", "telemetry.queryCredentialUsage", "verification.run"]
const protectedTools = ["provider.revokeCredential", "secretStore.destroyVersion"]
const requiredChecks: Record<string, string[]> = {
  trigger: ["request-authenticated", "source-deduplicated", "lease-held"],
  preflight: ["provider-ready", "credential-known", "scopes-known", "playbook-eligible", "management-authenticated", "store-ready", "consumers-known", "runtime-ready", "verifier-ready", "approvers-known", "overlap-supported", "mutation-declared", "no-conflict"],
  plan: ["plan-bound", "policy-approved", "plan-hashed", "recovery-ready"],
  create: ["replacement-created", "mutation-resolved", "generation-recorded"],
  store: ["secret-stored", "consumer-accessible", "plaintext-isolated"],
  deploy: ["candidate-deployed", "version-bound", "generation-tagged", "rollback-ready"],
  verify: ["provider-valid", "store-valid", "deployment-valid", "functional-valid", "downstream-valid", "telemetry-healthy", "coverage-complete", "rollback-ready"],
  rollout: ["production-promoted", "rollout-healthy"],
  observe: ["telemetry-healthy", "old-use-clear", "consumers-current"],
  approval: ["approval-valid", "action-digest-valid", "evidence-current"],
  revoke: ["old-revoked", "replacement-valid", "old-rejected", "old-secret-disabled"],
  complete: ["consumers-current", "replacement-valid", "old-rejected", "audit-complete"],
}

function identifier(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

export function PoliciesPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("all")
  const [selected, setSelected] = useState<Policy | null>(null)
  const [creating, setCreating] = useState(false)
  const query = useQuery({ queryKey: ["policies"], queryFn: () => api.getPolicies() })
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (query.data ?? []).filter((policy) => {
      const policyStatus = policy.active_version_id ? "active" : "draft"
      return (status === "all" || policyStatus === status) && (!term || policy.name.toLowerCase().includes(term))
    })
  }, [query.data, search, status])
  if (query.isLoading) return <div className="page"><Loading /></div>
  if (query.error) return <div className="page"><Failure error={query.error} /></div>

  if (creating) return <PolicySetup onClose={() => setCreating(false)} onCreated={async () => { await queryClient.invalidateQueries({ queryKey: ["policies"] }); setCreating(false) }} />

  if (selected) return <div className="page">
    <PageHeader eyebrow="Management / Policies" title={selected.name} onBack={() => setSelected(null)} />
    <div className="grid gap-5 xl:grid-cols-2">
      <Section title="Policy"><DetailList><Detail label="Version">{selected.latest_version}</Detail><Detail label="Status"><Badge variant={selected.active_version_id ? "healthy" : "warning"}>{selected.active_version_id ? "Active" : "Draft"}</Badge></Detail><Detail label="Updated">{formatDate(selected.updated_at, true)}</Detail><Detail label="Version ID">{selected.active_version_id ?? "Not activated"}</Detail></DetailList></Section>
      <Section title="Automation"><DetailList><Detail label="Automatic triggers">{selected.automatic_triggers?.map(titleCase).join(", ") ?? "Version details unavailable"}</Detail><Detail label="Approval required">{selected.protected_operations?.map(titleCase).join(", ") ?? "Version details unavailable"}</Detail><Detail label="Rollout">{selected.rollout?.map((value) => `${value}%`).join(" → ") ?? "Policy controlled"}</Detail><Detail label="Old generation">Preserved until verification passes</Detail></DetailList></Section>
    </div>
  </div>

  return <div className="page">
    <PageHeader eyebrow="Management" title="Policies" actions={<Button onClick={() => setCreating(true)}><Plus className="size-3.5" /> Add policy</Button>} />
    <Toolbar value={search} onChange={setSearch} placeholder="Search policies" resultCount={rows.length} resultLabel="policies" onClear={() => { setSearch(""); setStatus("all") }} filters={[{ label: "Status", value: status, defaultValue: "all", onChange: (event) => setStatus(event.target.value), children: <><option value="all">All statuses</option><option value="active">Active</option><option value="draft">Draft</option></> }]} />
    <Table><TableHeader><TableRow><TableHead>Policy</TableHead><TableHead>Version</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead className="w-36">Action</TableHead></TableRow></TableHeader><TableBody>{rows.map((policy) => <TableRow key={policy.id}><TableCell><button className="flex items-center gap-3 text-left font-medium hover:underline" onClick={() => setSelected(policy)}><Marker icon={ShieldCheck} />{policy.name}</button></TableCell><TableCell>{policy.latest_version}</TableCell><TableCell><Badge variant={policy.active_version_id ? "healthy" : "warning"}>{policy.active_version_id ? "Active" : "Draft"}</Badge></TableCell><TableCell className="text-[10px] text-[var(--ink-soft)]">{formatDate(policy.updated_at)}</TableCell><TableCell><Button variant="ghost" size="sm" onClick={() => setSelected(policy)}>View details <ChevronRight className="size-3.5" /></Button></TableCell></TableRow>)}</TableBody></Table>
  </div>
}

function PolicySetup({ onClose, onCreated }: { onClose: () => void; onCreated: () => Promise<void> }) {
  const [step, setStep] = useState(0)
  const [name, setName] = useState("")
  const [automaticTriggers, setAutomaticTriggers] = useState(["schedule", "expiry", "drift", "verified-exposure"])
  const [minimumConfidence, setMinimumConfidence] = useState<PolicyDefinition["minimum_automatic_confidence"]>("verified")
  const [expiryDays, setExpiryDays] = useState(7)
  const [observationMinutes, setObservationMinutes] = useState(30)
  const [metadataHours, setMetadataHours] = useState(24)
  const [recoveryModes, setRecoveryModes] = useState<PolicyDefinition["allowed_recovery_modes"]>(["rollback", "cleanup", "escalate"])
  const [preserveOld, setPreserveOld] = useState(true)
  const [functionalProbe, setFunctionalProbe] = useState(true)
  const [generationTelemetry, setGenerationTelemetry] = useState(true)
  const [runtimeAlignment, setRuntimeAlignment] = useState(true)
  const [activate, setActivate] = useState(true)
  const mutation = useMutation({ mutationFn: (input: CreatePolicyInput) => api.createPolicy(input), onSuccess: onCreated })

  const canContinue = step === 0 ? Boolean(name.trim()) : step === 1 ? automaticTriggers.length > 0 : step === 2 ? recoveryModes.length > 0 && observationMinutes >= 1 && expiryDays >= 1 && metadataHours >= 1 : true

  async function submit() {
    const policyId = identifier("policy")
    const emergencyTriggers = automaticTriggers.includes("verified-exposure") ? ["verified-exposure"] : []
    await mutation.mutateAsync({ policy_id: policyId, version_id: identifier("policy_version"), name: name.trim(), activate, definition: {
      required_checks: requiredChecks,
      allowed_tools: allowedTools,
      protected_tools: protectedTools,
      allowed_recovery_modes: recoveryModes,
      maximum_observation_seconds: observationMinutes * 60,
      preserve_old_generation: preserveOld,
      require_functional_probe: functionalProbe,
      require_generation_telemetry: generationTelemetry,
      rotate_before_expiry_seconds: expiryDays * 86400,
      maximum_metadata_age_seconds: metadataHours * 3600,
      require_runtime_alignment: runtimeAlignment,
      automatic_triggers: automaticTriggers,
      emergency_triggers: emergencyTriggers,
      minimum_automatic_confidence: minimumConfidence,
      probe_versions: {},
      recovery: {},
    } })
  }

  const primary = ["Continue to automation", "Continue to safety", "Review policy", activate ? "Create and activate" : "Save draft"][step]
  const evidenceOptions: Array<[string, boolean, (value: boolean) => void]> = [["Preserve old generation", preserveOld, setPreserveOld], ["Functional probe", functionalProbe, setFunctionalProbe], ["Generation telemetry", generationTelemetry, setGenerationTelemetry], ["Runtime alignment", runtimeAlignment, setRuntimeAlignment]]
  return <SetupPage eyebrow="Management / Policies" title="Add policy" steps={steps} current={step} onBack={() => setStep((value) => value - 1)} onCancel={onClose} error={mutation.error?.message} primary={step < 3 ? <Button onClick={() => setStep((value) => value + 1)} disabled={!canContinue}>{primary}</Button> : <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : primary}</Button>}>
    {step === 0 && <FormGrid><Field label="Policy name" wide><input className={formControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Production service credentials" /></Field><div className="rounded-xl border border-[var(--border-soft)] bg-[var(--surface-soft)] p-5 text-[10px] leading-5 text-[var(--ink-soft)] sm:col-span-2">Every policy includes FireKey’s twelve-stage evidence contract. This setup controls automation boundaries without weakening those required checks.</div></FormGrid>}
    {step === 1 && <FormGrid><Fieldset label="Automatic triggers" wide><div className="grid gap-2 sm:grid-cols-2">{triggers.map((trigger) => <label key={trigger} className="flex items-center gap-3 rounded-xl border border-[var(--border)] p-4 text-[10px] font-medium"><input type="checkbox" checked={automaticTriggers.includes(trigger)} onChange={(event) => setAutomaticTriggers((current) => event.target.checked ? [...current, trigger] : current.filter((item) => item !== trigger))} />{titleCase(trigger)}</label>)}</div></Fieldset><Field label="Minimum automatic confidence"><select className={formControl} value={minimumConfidence} onChange={(event) => setMinimumConfidence(event.target.value as typeof minimumConfidence)}><option value="verified">Verified</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></Field><Field label="Rotate before expiry" hint="Days before credential expiry."><input className={formControl} type="number" min="1" max="90" value={expiryDays} onChange={(event) => setExpiryDays(Number(event.target.value))} /></Field></FormGrid>}
    {step === 2 && <FormGrid><Field label="Observation window" hint="Minutes of healthy evidence required before revocation."><input className={formControl} type="number" min="1" max="10080" value={observationMinutes} onChange={(event) => setObservationMinutes(Number(event.target.value))} /></Field><Field label="Maximum metadata age" hint="Hours before inventory must be refreshed."><input className={formControl} type="number" min="1" max="720" value={metadataHours} onChange={(event) => setMetadataHours(Number(event.target.value))} /></Field><Fieldset label="Recovery modes" wide><div className="grid gap-2 sm:grid-cols-3">{(["rollback", "cleanup", "escalate"] as const).map((mode) => <label key={mode} className="flex items-center gap-3 rounded-xl border border-[var(--border)] p-4 text-[10px] font-medium"><input type="checkbox" checked={recoveryModes.includes(mode)} onChange={(event) => setRecoveryModes((current) => event.target.checked ? [...current, mode] : current.filter((item) => item !== mode))} />{titleCase(mode)}</label>)}</div></Fieldset><Fieldset label="Evidence requirements" wide><div className="grid gap-2 sm:grid-cols-2">{evidenceOptions.map(([label, value, setter]) => <label key={label} className="flex items-center gap-3 rounded-xl border border-[var(--border)] p-4 text-[10px] font-medium"><input type="checkbox" checked={value} onChange={(event) => setter(event.target.checked)} />{label}</label>)}</div></Fieldset></FormGrid>}
    {step === 3 && <div className="grid gap-5 lg:grid-cols-2"><Section title="Automation"><DetailList><Detail label="Name">{name}</Detail><Detail label="Triggers">{automaticTriggers.map(titleCase).join(", ")}</Detail><Detail label="Confidence">{titleCase(minimumConfidence)}</Detail><Detail label="Expiry lead">{expiryDays} days</Detail></DetailList></Section><Section title="Safety"><DetailList><Detail label="Approval required">Revoke credential, destroy secret</Detail><Detail label="Observation">{observationMinutes} minutes</Detail><Detail label="Recovery">{recoveryModes.map(titleCase).join(", ")}</Detail><Detail label="Status">{activate ? "Activate after creation" : "Save as draft"}</Detail></DetailList></Section><label className="flex items-center gap-3 rounded-xl border border-[var(--border)] p-4 text-[10px] font-medium lg:col-span-2"><input type="checkbox" checked={activate} onChange={(event) => setActivate(event.target.checked)} /> Activate this policy after creation</label></div>}
  </SetupPage>
}
