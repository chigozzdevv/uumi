import { createServer } from "node:http"
import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto"
import { createStore } from "./data.mjs"

const port = Number(process.env.FIREKEY_MOCK_PORT ?? 8787)
const organisationRoot = "/v1/organisations/org_acme"
const store = createStore()
const githubOnboardings = new Map()
const googleCloudOnboardings = new Map()
const walkthroughUploads = new Set()

function json(response, status, body) {
  response.writeHead(status, {
    "Access-Control-Allow-Headers": "Content-Type, Content-Range, Idempotency-Key",
    "Access-Control-Allow-Methods": "GET, PATCH, POST, PUT, OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "X-FireKey-Source": "mock-server",
  })
  response.end(JSON.stringify(body))
}

async function body(request) {
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  if (!chunks.length) return {}
  return JSON.parse(Buffer.concat(chunks).toString("utf8"))
}

function item(items, id) {
  return items.find((entry) => entry.id === id)
}

function emailEndpoint(endpoint) {
  return {
    id: endpoint.id,
    organisation_id: endpoint.organisation_id,
    email_address: endpoint.recipients[0],
    event_kinds: endpoint.event_kinds,
    enabled: endpoint.enabled,
    created_at: endpoint.created_at,
    updated_at: endpoint.updated_at,
    revision: endpoint.revision,
  }
}

function notificationTopicsForRole(role) {
  if (role === "administrator") return store.notificationTopics
  const allowed = role === "operator"
    ? new Set(["incidents", "rotation-failures", "credential-use", "rotation-due", "rotation-completed"])
    : new Set(["credential-use", "rotation-completed"])
  return store.notificationTopics.filter((topic) => allowed.has(topic.id))
}

function active(items) {
  return items.filter((entry) => !entry.archived_at)
}

function runtimeBindingMatches(resource, secretResource, secretReference, variable, container) {
  const project = resource.reference.startsWith("projects/") ? resource.reference.split("/")[1] : ""
  const version = secretReference.split("/versions/")[1]
  return (resource.secret_bindings ?? []).filter((binding) => {
    const bindingResource = binding.secret.startsWith("projects/") ? binding.secret : `projects/${project}/secrets/${binding.secret}`
    return binding.name === variable && (binding.container ?? null) === (container ?? null) && bindingResource === secretResource && binding.version === version
  }).length === 1
}

function validProviderAdapter(http) {
  if (!http || typeof http !== "object") return false
  let base
  try { base = new URL(http.base_url) } catch { return false }
  if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash) return false
  const operation = (value) => value && typeof value === "object" && typeof value.path === "string" && value.path.startsWith("/") && !value.path.includes("://") && Array.isArray(value.success_statuses) && value.success_statuses.length > 0
  return operation(http.list_credentials)
    && operation(http.create_credential)
    && operation(http.revoke_credential)
    && operation(http.test_credential)
    && typeof http.list_credentials.provider_id_field === "string"
    && typeof http.create_credential.provider_id_field === "string"
    && typeof http.create_credential.secret_field === "string"
    && http.revoke_credential.path.includes("{provider_id}")
    && typeof http.test_credential.provider_id_field === "string"
    && ["bearer", "header", "basic"].includes(http.auth?.scheme)
    && ["bearer", "header", "basic"].includes(http.credential_auth?.scheme)
  }

function revise(response, current, input, fields) {
  if (current.archived_at) {
    json(response, 409, { code: "conflict", message: "Archived resources cannot be changed" })
    return false
  }
  if (current.revision !== input.expected_revision) {
    json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${current.revision}; reload and try again` })
    return false
  }
  const changed = fields.filter((field) => Object.hasOwn(input, field))
  if (!changed.length) {
    json(response, 422, { code: "validation-error", message: "At least one editable field is required" })
    return false
  }
  for (const field of changed) current[field] = input[field]
  current.updated_at = new Date().toISOString()
  current.revision += 1
  return true
}

function archive(response, current, input) {
  if (current.archived_at) return json(response, 409, { code: "conflict", message: "Resource is already archived" })
  if (current.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${current.revision}; reload and try again` })
  current.archived_at = new Date().toISOString()
  current.updated_at = current.archived_at
  current.revision += 1
  return json(response, 200, current)
}

function archiveRecord(current, timestamp) {
  if (current.archived_at) return
  current.archived_at = timestamp
  current.updated_at = timestamp
  current.revision += 1
}

function cascadeInventory(collectionName, resourceId) {
  const timestamp = new Date().toISOString()
  const serviceIds = new Set()
  const environmentIds = new Set()
  const credentialIds = new Set()
  if (collectionName === "connections") {
    for (const service of active(store.services)) {
      if (service.runtime_connection_id === resourceId || service.telemetry_connection_ids.includes(resourceId)) serviceIds.add(service.id)
    }
    for (const credential of active(store.credentials)) {
      if ([credential.connection_id, credential.secret_store_connection_id].includes(resourceId)) credentialIds.add(credential.id)
    }
  }
  if (collectionName === "applications") {
    for (const environment of active(store.environments)) if (environment.application_id === resourceId) environmentIds.add(environment.id)
    for (const service of active(store.services)) if (service.application_id === resourceId) serviceIds.add(service.id)
  }
  if (collectionName === "environments") {
    for (const service of active(store.services)) if (service.environment_id === resourceId) serviceIds.add(service.id)
  }
  if (collectionName === "services") serviceIds.add(resourceId)
  if (collectionName === "credentials") credentialIds.add(resourceId)
  for (const binding of store.bindings) if (serviceIds.has(binding.service_id)) credentialIds.add(binding.credential_id)
  for (const credential of active(store.credentials)) {
    if (credentialIds.has(credential.id) && !(collectionName === "credentials" && credential.id === resourceId)) archiveRecord(credential, timestamp)
  }
  for (const service of active(store.services)) {
    if (serviceIds.has(service.id) && !(collectionName === "services" && service.id === resourceId)) archiveRecord(service, timestamp)
  }
  for (const environment of active(store.environments)) if (environmentIds.has(environment.id)) archiveRecord(environment, timestamp)
  for (let index = store.bindings.length - 1; index >= 0; index -= 1) {
    if (credentialIds.has(store.bindings[index].credential_id)) store.bindings.splice(index, 1)
  }
  store.overview.credentials = active(store.credentials).length
}

function audit(kind, resource, revision) {
  const sequence = Math.max(0, ...store.audits.map((event) => event.sequence)) + 1
  store.audits.unshift({ id: `audit_mock_${sequence}`, organisation_id: "org_acme", sequence, kind, actor_id: "actor_chigozie", resource, run_id: null, payload: { revision }, evidence_ids: [], previous_hash: "0".repeat(64), event_hash: createHash("sha256").update(`${kind}:${resource}:${revision}`).digest("hex"), occurred_at: new Date().toISOString(), region: "us-central1" })
}

const controlStages = ["trigger", "preflight", "plan", "create", "store", "deploy", "verify", "rollout", "observe", "approval", "revoke", "complete"]

function refreshOverview() {
  store.overview.credentials = active(store.credentials).length
  store.overview.rotations_in_progress = store.runs.filter((run) => ["pending", "running", "paused", "recovering", "cleanup-required"].includes(run.status)).length
  store.overview.failed_rotations = store.runs.filter((run) => run.status === "failed").length
  store.overview.pending_approvals = store.approvals.filter((approval) => approval.decision === "pending" && Date.parse(approval.expires_at) > Date.now()).length
}

function moveRun(run, stage, status) {
  if (!run || ["cancelled", "completed", "compensated", "failed"].includes(run.status)) return
  const timestamp = new Date().toISOString()
  if (stage === "plan" && !run.plan_id) {
    run.plan_id = `plan_${run.id.replace(/^run_/, "")}`
    run.plan_hash = createHash("sha256").update(`${run.id}:plan`).digest("hex")
  }
  if (stage === "create" && !run.target_generation_id) run.target_generation_id = `gen_${randomBytes(8).toString("hex")}`
  run.stage = stage
  run.status = status
  run.updated_at = timestamp
  run.revision += 1
  run.lease = status === "running" ? { owner_id: "actor_coordinator", fencing_token: run.fencing_token, expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString() } : null
  refreshOverview()
}

function createMockApproval(run) {
  const existing = store.approvals.find((approval) => approval.run_id === run.id && approval.decision === "pending")
  if (existing) return existing
  const timestamp = new Date().toISOString()
  const suffix = randomBytes(8).toString("hex")
  const approval = {
    id: `approval_${suffix}`,
    organisation_id: run.organisation_id,
    run_id: run.id,
    action_id: `action_revoke_${suffix}`,
    action_digest: createHash("sha256").update(`${run.id}:action:${run.revision}`).digest("hex"),
    plan_hash: run.plan_hash,
    evidence_hash: createHash("sha256").update(`${run.id}:evidence:${run.revision}`).digest("hex"),
    generation_id: run.current_generation_id,
    requested_by: "actor_coordinator",
    capability_hash: createHash("sha256").update(`${run.id}:capability:${run.revision}`).digest("hex"),
    decision: "pending",
    approver_id: null,
    expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    created_at: timestamp,
    decided_at: null,
    consumed_at: null,
    revision: 0,
  }
  store.approvals.unshift(approval)
  refreshOverview()
  return approval
}

function requiresRevocationApproval(run) {
  return item(store.controlVersions, run.control_version)?.definition?.require_revoke_approval === true
}

function scheduleMockRotation(run) {
  const step = Math.max(100, Number(process.env.FIREKEY_MOCK_ROTATION_STEP_MS ?? 2000))
  const transitions = ["preflight", "plan", "create", "store", "deploy", "verify", "rollout", "observe"].map((stage, index) => [(index + 1) * step, stage])
  for (const [delay, stage] of transitions) {
    setTimeout(() => {
      if (["pending", "running"].includes(run.status)) moveRun(run, stage, "running")
    }, delay)
  }
  setTimeout(() => {
    if (!["pending", "running"].includes(run.status)) return
    if (requiresRevocationApproval(run)) {
      moveRun(run, "approval", "paused")
      createMockApproval(run)
      return
    }
    moveRun(run, "approval", "running")
    setTimeout(() => moveRun(run, "revoke", "running"), step)
    setTimeout(() => moveRun(run, "complete", "completed"), 2 * step)
  }, 9 * step)
}

const stageChecks = {
  trigger: ["request-authenticated", "source-deduplicated", "lease-held"],
  preflight: ["provider-ready", "credential-known", "scopes-known", "playbook-eligible", "management-authenticated", "store-ready", "consumers-known", "runtime-ready", "verifier-ready", "approvers-known", "overlap-supported", "mutation-declared", "no-conflict"],
  plan: ["plan-bound", "controls-pinned", "plan-hashed", "recovery-ready"],
  create: ["replacement-created", "mutation-resolved", "generation-recorded"],
  store: ["secret-stored", "consumer-accessible", "plaintext-isolated"],
  deploy: ["candidate-deployed", "version-bound", "generation-tagged", "rollback-ready"],
  verify: ["provider-valid", "store-valid", "deployment-valid", "telemetry-healthy", "coverage-complete", "rollback-ready"],
  rollout: ["production-promoted", "rollout-healthy"],
  observe: ["telemetry-healthy", "old-use-clear", "consumers-current"],
  approval: ["approval-valid", "action-digest-valid", "evidence-current"],
  revoke: ["old-revoked", "replacement-valid", "old-rejected", "old-secret-disabled"],
  complete: ["consumers-current", "replacement-valid", "old-rejected", "audit-complete"],
}

function human(value) {
  const terms = { api: "API", github: "GitHub", oauth: "OAuth" }
  return value.replace(/[._-]+/g, " ").split(/\s+/).filter(Boolean).map((word) => terms[word.toLowerCase()] ?? `${word[0].toUpperCase()}${word.slice(1)}`).join(" ")
}

function triggerStory(source, kind) {
  if (["credential-rotation-due", "credential-expiring"].includes(kind)) return { summary: "Rotation started on schedule", configured: "Scheduled rotation" }
  if (kind === "credential-exposure-detected") return { summary: "Exposure alert started rotation", configured: human(source) }
  if (["credential-inventory-drift", "credential-provider-drift", "credential-runtime-drift"].includes(kind)) return { summary: "Configuration drift started rotation", configured: "Configuration drift" }
  if (kind === "manual" || ["manual", "console", "dashboard"].includes(source)) return { summary: "Rotation started manually", configured: "Manual rotation" }
  if (["schedule", "scheduler"].includes(source)) return { summary: "Rotation started on schedule", configured: "Scheduled rotation" }
  if (source === "github-secret-scanning") return { summary: "Exposure alert started rotation", configured: "GitHub Secret Scanning" }
  if (source === "secret-manager") return { summary: "Secret-store event started rotation", configured: "Secret Manager schedule" }
  const configured = human(source)
  return { summary: `${configured} started rotation`, configured }
}

function credentialServices(run) {
  const credential = item(store.credentials, run.credential_id)
  return [...new Set(store.bindings
    .filter((binding) => binding.credential_id === credential?.id)
    .map((binding) => item(store.services, binding.service_id))
    .filter(Boolean))]
}

function approvalPresentation(run) {
  if (!requiresRevocationApproval(run)) return { summary: "Approval not required", details: [] }
  const approvals = store.approvals.filter((approval) => approval.run_id === run.id)
  const decisions = new Set(approvals.map((approval) => approval.decision))
  const details = approvals.length > 1 ? [{ label: "Protected actions", value: String(approvals.length) }] : []
  if (decisions.has("rejected")) return { summary: "Revocation rejected", details }
  if (decisions.has("cancelled")) return { summary: "Revocation cancelled", details }
  if (decisions.has("more-evidence")) return { summary: "More evidence requested", details }
  if (decisions.has("extend-observation")) return { summary: "Observation extended", details }
  if (decisions.has("pending")) return { summary: "Waiting for revocation approval", details }
  if (approvals.length && [...decisions].every((decision) => decision === "approved")) return { summary: "Revocation approved", details }
  return { summary: "Revocation approval requested", details }
}

function stagePresentation(run, stage, status) {
  if (!["succeeded", "recovered"].includes(status)) {
    if (stage === "approval") return approvalPresentation(run)
    if (run.failure?.code === "provider-authentication-expired") return { summary: null, details: [] }
    if (run.browser_playbook_version) return { summary: "Computer Use paused", details: [{ label: "Method", value: "Computer Use" }] }
    return { summary: null, details: [] }
  }
  if (stage === "trigger") {
    const story = triggerStory(run.trigger.source, run.trigger.kind)
    return { summary: story.summary, details: [{ label: "Configured trigger", value: story.configured }, { label: "Reason", value: run.trigger.reason }] }
  }
  if (stage === "preflight") {
    return { summary: "Ready to rotate", details: [{ label: "Connections", value: "3 ready" }, ...(requiresRevocationApproval(run) ? [{ label: "Approval", value: "Available" }] : []), ...(run.browser_playbook_version ? [{ label: "Playbook", value: "Pinned" }] : [])] }
  }
  if (stage === "plan") return { summary: "5% → 25% → 50% → 100% rollout", details: [{ label: "Observation", value: "30 minutes" }, { label: "Recovery", value: "Branches pinned" }] }
  if (stage === "create") return { summary: "Replacement created", details: [{ label: "Method", value: run.browser_playbook_version ? "Computer Use" : "Provider API" }] }
  if (stage === "store") {
    const credential = item(store.credentials, run.credential_id)
    const secretStore = item(store.connections, credential?.secret_store_connection_id)
    const version = credential?.secret_reference?.split("/").at(-1)
    return { summary: "Secret verified", details: [{ label: "Secret store", value: secretStore?.display_name.split("·")[0].trim() ?? "Configured store" }, { label: "Version", value: version ? `${version} enabled` : "Enabled" }, { label: "Consumer access", value: credential?.consumer_ids.length ? `Confirmed for ${credential.consumer_ids.length}` : "Confirmed" }] }
  }
  if (stage === "deploy") {
    const services = credentialServices(run).map((service) => service.display_name)
    return { summary: services.length === 1 ? `Candidate running on ${services[0]}` : services.length ? `Candidates running on ${services.length} services` : "Candidate running", details: services.length > 1 ? [{ label: "Services", value: services.join(", ") }] : [] }
  }
  if (stage === "verify") return { summary: "Deployment verified", details: [{ label: "Provider", value: "Replacement valid" }, { label: "Secret store", value: "Version enabled" }, { label: "Runtime", value: "Candidate running" }, ...(credentialServices(run).some((service) => service.telemetry_connection_ids.length) ? [{ label: "Telemetry", value: "Healthy" }] : [])] }
  if (stage === "rollout") return { summary: "100% on replacement", details: [{ label: "Milestones", value: "5% → 25% → 50% → 100%" }] }
  if (stage === "observe") return { summary: "30 minutes observation passed", details: [...(credentialServices(run).some((service) => service.telemetry_connection_ids.length) ? [{ label: "Telemetry", value: "Healthy" }] : []), { label: "Previous credential use", value: "Not detected" }] }
  if (stage === "approval") return approvalPresentation(run)
  if (stage === "revoke") return { summary: "Previous credential revoked", details: [{ label: "Old credential", value: "Rejected" }, { label: "Replacement", value: "Valid" }, { label: "Previous secret version", value: "Disabled" }] }
  if (stage === "complete") return { summary: "Rotation complete", details: [] }
  return { summary: null, details: [] }
}

function runPlaybookSteps(run, stage) {
  const version = item(store.playbookVersions, run.browser_playbook_version)
  return version?.definition?.steps?.filter((step) => step.stage === stage) ?? []
}

function runComputerUse(run) {
  if (!run.browser_playbook_version || run.failure?.code === "provider-authentication-expired") return []
  const currentIndex = controlStages.indexOf(run.stage)
  const definitions = []
  for (const stage of ["create", "revoke"]) {
    for (const [stepIndex, step] of runPlaybookSteps(run, stage).filter((entry) => entry.operation !== "navigate").entries()) {
      const selector = step.selectors?.[0]?.value
      definitions.push({
        stage,
        stepIndex,
        turn: definitions.length + 1,
        stepId: step.id,
        effect: step.effect,
        prompt: step.objective,
        thought: step.effect === "create-credential"
          ? "The credential creation form and submit control match the playbook checkpoint."
          : step.effect === "revoke-credential"
            ? "The previous credential and revoke control match the expected checkpoint."
            : "The current page matches this playbook step's checkpoint.",
        intent: step.objective,
        target: selector ? selector.replaceAll("-", " ") : step.operation,
      })
    }
  }
  return definitions.flatMap((definition) => {
    const stageIndex = controlStages.indexOf(definition.stage)
    if (currentIndex < stageIndex) return []
    const started = new Date(run.created_at).getTime() + stageIndex * 60_000 + definition.stepIndex * 12_000
    const key = `${run.id}_${definition.stepId}`
    const base = { organisation_id: run.organisation_id, session_id: `browser_${run.id}`, run_id: run.id, step_id: definition.stepId, stage: definition.stage, turn: definition.turn, effect: null }
    const events = [
      { ...base, id: `activity_${key}_input`, phase: "input", status: "sent", effect: definition.effect, prompt: definition.prompt, instruction: "Use only the typed action permitted by this playbook step.", image_reference: `mock://computer-use/${key}/input`, image_digest: "a".repeat(64), content: null, action: null, arguments: {}, intent: null, safety_decision: null, target: null, recorded_at: new Date(started + 5_000).toISOString() },
      { ...base, id: `activity_${key}_thought`, phase: "thought", status: "streaming", prompt: null, instruction: null, image_reference: null, image_digest: null, content: definition.thought, action: null, arguments: {}, intent: null, safety_decision: null, target: null, recorded_at: new Date(started + 6_000).toISOString() },
      { ...base, id: `activity_${key}_proposal`, phase: "proposal", status: "proposed", prompt: null, instruction: null, image_reference: null, image_digest: null, content: null, action: "click", arguments: { x: 812, y: 704, intent: definition.intent, safety_decision: { decision: "allowed" } }, intent: definition.intent, safety_decision: "allowed", target: definition.target, recorded_at: new Date(started + 8_000).toISOString() },
      { ...base, id: `activity_${key}_validation`, phase: "validation", status: "validated", prompt: null, instruction: null, image_reference: null, image_digest: null, content: null, action: "click", arguments: {}, intent: null, safety_decision: null, target: definition.target, recorded_at: new Date(started + 9_000).toISOString() },
    ]
    if (currentIndex > stageIndex || run.status === "completed") events.push({ ...base, id: `activity_${key}_execution`, phase: "execution", status: "succeeded", prompt: null, instruction: null, image_reference: null, image_digest: null, content: null, action: "click", arguments: {}, intent: null, safety_decision: null, target: definition.target, recorded_at: new Date(started + 11_000).toISOString() })
    return events
  })
}

function browserActions(run, stage) {
  return runPlaybookSteps(run, stage).map((step) => ({
    step_id: step.id,
    objective: step.objective,
    operation: step.operation,
    outcome: step.effect === "create-credential"
      ? "Secret captured and masked"
      : step.effect === "revoke-credential"
        ? "Previous credential revoked"
        : step.operation === "navigate" ? "Page opened" : "Step completed",
  }))
}

function checksFor(run, stage) {
  return item(store.controlVersions, run.control_version)?.definition?.required_checks?.[stage]
    ?? stageChecks[stage]
}

function runHistory(run) {
  const current = controlStages.indexOf(run.stage)
  const completed = run.status === "completed" ? controlStages.length - 1 : current - 1
  const activities = controlStages.slice(0, completed + 1).map((stage, index) => ({
    id: `stage_${run.id}_${stage}`,
    stage,
    status: "succeeded",
    checks: checksFor(run, stage),
    evidence_count: Math.max(1, Math.min(3, checksFor(run, stage).length)),
    ...stagePresentation(run, stage, "succeeded"),
    agent_decisions: stage === "preflight" ? (() => {
      const consumers = item(store.credentials, run.credential_id)?.consumer_ids.length ?? 0
      const noun = consumers === 1 ? "consumer" : "consumers"
      return [{ agent: "inventory", decision: "Consumer inventory confirmed", explanation: consumers ? `No missing or stale mappings were found across ${consumers} declared ${noun}.` : "No missing or stale consumer mappings were found." }]
    })() : stage === "plan" ? [{ agent: "planner", decision: "Use Dual Slot strategy", explanation: "Keep the previous generation available until the replacement is deployed and verified." }] : run.browser_playbook_version && ["create", "revoke"].includes(stage) ? [{ agent: "operator", decision: "Continue browser step", explanation: "The browser step matched its expected checkpoint." }] : [],
    browser_actions: run.browser_playbook_version ? browserActions(run, stage) : [],
    reason: null,
    retryable: false,
    started_at: new Date(new Date(run.created_at).getTime() + index * 60_000).toISOString(),
    completed_at: new Date(new Date(run.created_at).getTime() + index * 60_000 + 45_000).toISOString(),
  }))
  if (["paused", "failed", "cleanup-required"].includes(run.status)) {
    activities.push({
      id: `stage_${run.id}_${run.stage}_current`,
      stage: run.stage,
      status: run.status === "paused" ? "paused" : "failed",
      checks: [],
      evidence_count: run.failure?.evidence_ids?.length ?? 0,
      ...stagePresentation(run, run.stage, run.status === "paused" ? "paused" : "failed"),
      agent_decisions: run.browser_playbook_version && run.stage === "create" && run.failure?.code !== "provider-authentication-expired" ? [{ agent: "operator", decision: "Step paused", explanation: "The browser step paused because its safety checkpoint did not pass." }] : [],
      browser_actions: run.browser_playbook_version && run.stage === "create" && run.failure?.code !== "provider-authentication-expired" ? browserActions(run, "create").slice(-1).map((step) => ({ ...step, outcome: "Step paused" })) : [],
      reason: run.stage === "approval" && run.status === "paused" ? null : run.failure?.message ?? null,
      retryable: run.failure?.retryable ?? false,
      started_at: run.updated_at,
      completed_at: run.updated_at,
    })
  }
  return { run_id: run.id, stages: activities, computer_use: runComputerUse(run) }
}

function computerUseInputImage(stage) {
  const revoke = stage === "revoke"
  const button = revoke ? "Revoke credential" : "Create credential"
  const buttonX = revoke ? 888 : 850
  const textX = revoke ? 925 : 907
  const buttonFill = revoke ? "#ffffff" : "#25282c"
  const buttonStroke = revoke ? "#b9bdc3" : "#25282c"
  const buttonText = revoke ? "#202327" : "#ffffff"
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#f4f5f6"/><rect x="110" y="70" width="1060" height="580" rx="18" fill="#fff" stroke="#d8dadd"/><text x="170" y="145" font-family="Arial" font-size="30" font-weight="700" fill="#202327">API credentials</text><text x="170" y="200" font-family="Arial" font-size="18" fill="#676d76">Manage credentials for this account.</text><line x1="170" y1="255" x2="1110" y2="255" stroke="#d8dadd"/><text x="170" y="320" font-family="Arial" font-size="19" font-weight="700" fill="#202327">${revoke ? "Previous credential" : "Production credential"}</text><text x="170" y="360" font-family="Arial" font-size="16" fill="#676d76">Active</text><rect x="${buttonX}" y="520" width="222" height="62" rx="12" fill="${buttonFill}" stroke="${buttonStroke}"/><text x="${textX}" y="559" font-family="Arial" font-size="18" font-weight="700" fill="${buttonText}">${button}</text></svg>`
}

function validControlDefinition(definition) {
  return Boolean(
    definition
    && controlStages.every((stage) => Array.isArray(definition.required_checks?.[stage]) && definition.required_checks[stage].length)
    && Array.isArray(definition.allowed_tools)
    && definition.allowed_tools.length
    && Array.isArray(definition.allowed_recovery_modes)
    && definition.allowed_recovery_modes.length
    && typeof definition.require_revoke_approval === "boolean"
    && (!definition.require_generation_telemetry || definition.allowed_tools.includes("verification.run"))
    && (definition.protected_tools ?? []).every((tool) => definition.allowed_tools.includes(tool)),
  )
}

function validControlPreferences(preferences) {
  const supported = new Set(["expiry", "drift", "verified-exposure"])
  const exposureSources = preferences?.exposure_sources
  const exposureEnabled = preferences?.automatic_triggers?.includes("verified-exposure")
  return Boolean(
    preferences
    && Array.isArray(preferences.automatic_triggers)
    && preferences.automatic_triggers.length
    && preferences.automatic_triggers.every((trigger) => supported.has(trigger))
    && preferences.rotate_before_expiry_seconds >= 300
    && preferences.maximum_observation_seconds >= 60
    && typeof preferences.require_revoke_approval === "boolean"
    && Array.isArray(exposureSources)
    && exposureEnabled === Boolean(exposureSources.length),
  )
}

function applyControlPreferences(definition, preferences) {
  const events = {
    expiry: ["credential-expiring"],
    drift: ["credential-inventory-drift", "credential-provider-drift", "credential-runtime-drift"],
    "verified-exposure": ["credential-exposure-detected"],
  }
  const next = {
    ...structuredClone(definition),
    automatic_triggers: preferences.automatic_triggers.flatMap((trigger) => events[trigger]),
    emergency_triggers: preferences.automatic_triggers.includes("verified-exposure") ? ["credential-exposure-detected"] : [],
    exposure_sources: structuredClone(preferences.exposure_sources),
    rotate_before_expiry_seconds: preferences.rotate_before_expiry_seconds,
    maximum_observation_seconds: preferences.maximum_observation_seconds,
    require_revoke_approval: preferences.require_revoke_approval,
  }
  if (preferences.require_revoke_approval) {
    next.required_checks.preflight = structuredClone(stageChecks.preflight)
    next.required_checks.approval = structuredClone(stageChecks.approval)
    next.protected_tools = [
      next.allowed_tools.includes("browser.revokeCredential")
        ? "browser.revokeCredential"
        : "provider.revokeCredential",
      "secretStore.disableVersion",
      "secretStore.destroyVersion",
    ]
  } else {
    next.required_checks.preflight = next.required_checks.preflight.filter((check) => check !== "approvers-known")
    next.required_checks.approval = ["approval-not-required", "evidence-current"]
    next.protected_tools = []
  }
  return next
}

function sanitiseSource(value) {
  let text = String(value ?? "").trim()
  let redactions = 0
  const patterns = [
    /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
    /\b(?:authorization|password|passphrase|api[_-]?key|credential|secret|token)\b\s*[:=]\s*[^\s,;]+/gi,
    /\bbearer\s+[A-Za-z0-9._~+/=-]{12,}/gi,
    /\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|sk_(?:live|test)_[A-Za-z0-9]{16,})\b/g,
    /\b[A-Za-z0-9_-]{32,}\b/g,
  ]
  for (const pattern of patterns) text = text.replace(pattern, () => { redactions += 1; return "[REDACTED]" })
  return { text, redactions }
}

function generatedPlaybook(objective) {
  const exactName = String(objective).match(/exact name "([^"]+)"/)?.[1]
  const exactPlatform = String(objective).match(/exact platform "([^"]+)"/)?.[1]
  const platform = exactPlatform ?? "browser-provider"
  const connection = store.connections.find((entry) => entry.interface === "browser" && entry.platform === platform)
  const domain = (connection?.allowed_resources?.[0] ?? `${platform}.example.com`).replace(/^\*\./, "console.")
  const selector = (value) => ({ kind: "test-id", value, name: null, exact: true })
  const checkpoint = { url_pattern: `https://${domain}/**`, required_text: [], forbidden_text: [] }
  const step = (id, stage, effect, tool, operation, objectiveText, target, secure = false) => ({
    id,
    stage,
    effect,
    tool,
    operation,
    objective: objectiveText,
    parameters: {},
    protected: false,
    evidence_checks: [effect === "create-credential" ? "credential-captured" : effect === "revoke-credential" ? "credential-revoked" : "checkpoint-confirmed"],
    selectors: operation === "navigate" ? [] : [selector(target)],
    checkpoint: structuredClone(checkpoint),
    secure_field: secure ? { name: "credential", selector: selector("generated-credential"), provider_id_selector: selector("credential-id") } : null,
    outputs: [],
    timeout_seconds: 30,
    retry_limit: 1,
  })
  return {
    name: exactName ?? `${platform.replaceAll("-", " ")} credential rotation`,
    platform,
    allowed_domains: connection?.allowed_resources?.length ? structuredClone(connection.allowed_resources) : [domain],
    login_url_pattern: `https://${domain}/login*`,
    steps: [
      step("action_1", "create", "none", "browser.navigate", "navigate", "Open the API credentials page", "", false),
      step("action_2", "create", "none", "browser.click", "click", "Open the credential creation form", "create-credential", false),
      step("action_3", "create", "create-credential", "browser.secure-capture", "capture", "Submit the credential creation form", "confirm-create-credential", true),
      step("action_4", "revoke", "none", "browser.click", "click", "Select the previous credential", "previous-credential", false),
      step("action_5", "revoke", "revoke-credential", "browser.revokeCredential", "revoke", "Revoke the previous credential", "revoke-credential", false),
    ],
  }
}

createServer(async (request, response) => {
  if (request.method === "OPTIONS") return json(response, 204, {})
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`)
  await new Promise((resolve) => setTimeout(resolve, 90))

  if (request.method === "GET" && url.pathname === "/health") return json(response, 200, { status: "ok", service: "firekey-mock" })
  if (request.method === "POST" && url.pathname === "/v1/auth/logout") {
    response.writeHead(204, { "Cache-Control": "no-store", "Clear-Site-Data": '"cache", "cookies", "storage"', "X-FireKey-Source": "mock-server" })
    return response.end()
  }
  if (!url.pathname.startsWith(organisationRoot)) return json(response, 404, { code: "not-found", message: "Mock route not found" })

  const path = url.pathname.slice(organisationRoot.length)
  if (request.method === "POST" && path === "/google-cloud/onboarding") {
    const timestamp = new Date()
    const state = `mock-google-state-${randomBytes(24).toString("hex")}`
    const sessionId = `google_${randomUUID().replaceAll("-", "").slice(0, 16)}`
    const expires = new Date(timestamp.getTime() + 15 * 60 * 1000).toISOString()
    const session = { id: sessionId, organisation_id: "org_acme", subject: "user_chigozie", state_hash: "0".repeat(64), verifier_hash: "1".repeat(64), status: "pending", projects: [], connection_id: null, created_at: timestamp.toISOString(), expires_at: expires, completed_at: null }
    googleCloudOnboardings.set(sessionId, { session, state, projects: [] })
    return json(response, 201, {
      session,
      state,
      pkce_verifier: "mock-google-pkce-verifier-abcdefghijklmnopqrstuvwxyz-1234567890",
      authorization_url: `http://127.0.0.1:5173/?google_cloud=callback&code=mock-google-code&state=${encodeURIComponent(state)}`,
    })
  }
  const googleCloudOnboardingMatch = path.match(/^\/google-cloud\/onboarding\/([a-z0-9_-]+)$/)
  if (request.method === "POST" && googleCloudOnboardingMatch) {
    const input = await body(request)
    if (!input.state || !input.pkce_verifier || !input.code) return json(response, 422, { code: "validation-error", message: "Google Cloud callback is incomplete" })
    const timestamp = new Date().toISOString()
    const projects = [
        {
          project_id: "acme-prod",
          project_number: "123456789012",
          display_name: "Acme Production",
          services: [
            { reference: "projects/acme-prod/locations/us-central1/services/notification-worker", display_name: "notification-worker", region: "us-central1", runtime_identity: "notification-worker@acme-prod.iam.gserviceaccount.com" },
            { reference: "projects/acme-prod/locations/us-central1/services/checkout-api", display_name: "checkout-api", region: "us-central1", runtime_identity: "checkout-api@acme-prod.iam.gserviceaccount.com" },
            { reference: "projects/acme-prod/locations/europe-west1/services/billing-api", display_name: "billing-api", region: "europe-west1", runtime_identity: "billing-api@acme-prod.iam.gserviceaccount.com" },
          ],
          service_accounts: [
            { email: "firekey-automation@acme-prod.iam.gserviceaccount.com", display_name: "FireKey automation" },
          ],
        },
        {
          project_id: "acme-staging",
          project_number: "123456789013",
          display_name: "Acme Staging",
          services: [
            { reference: "projects/acme-staging/locations/us-central1/services/notification-worker", display_name: "notification-worker", region: "us-central1", runtime_identity: "notification-worker@acme-staging.iam.gserviceaccount.com" },
          ],
          service_accounts: [
            { email: "firekey-automation@acme-staging.iam.gserviceaccount.com", display_name: "FireKey automation" },
          ],
        },
      ]
    const onboarding = googleCloudOnboardings.get(googleCloudOnboardingMatch[1])
    const session = { ...(onboarding?.session ?? {}), id: googleCloudOnboardingMatch[1], organisation_id: "org_acme", subject: "user_chigozie", state_hash: "0".repeat(64), verifier_hash: "1".repeat(64), status: "complete", projects, connection_id: null, created_at: onboarding?.session.created_at ?? timestamp, expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(), completed_at: timestamp }
    googleCloudOnboardings.set(session.id, { session, state: input.state, projects })
    return json(response, 200, { session, projects })
  }
  const googleCloudConnectionMatch = path.match(/^\/google-cloud\/onboarding\/([a-z0-9_-]+)\/connection$/)
  if (request.method === "POST" && googleCloudConnectionMatch) {
    const input = await body(request)
    const onboarding = googleCloudOnboardings.get(googleCloudConnectionMatch[1])
    const project = onboarding?.projects.find((entry) => entry.project_id === input.project_id)
    const account = project?.service_accounts.find((entry) => entry.email === input.automation_identity)
    if (!project || !account) return json(response, 404, { code: "not-found", message: "Selected Google Cloud project or identity was not discovered" })
    if (onboarding.session.connection_id) {
      const existing = item(store.connections, onboarding.session.connection_id)
      if (!existing) return json(response, 409, { code: "conflict", message: "Google Cloud connection is unavailable" })
      return json(response, 201, { connection: existing, grant_command: `gcloud iam service-accounts add-iam-policy-binding ${account.email} --project=${project.project_id} --member=serviceAccount:firekey-broker@firekey-demo.iam.gserviceaccount.com --role=roles/iam.serviceAccountTokenCreator` })
    }
    const timestamp = new Date().toISOString()
    const connection = {
      id: `conn_${randomUUID().replaceAll("-", "").slice(0, 16)}`, organisation_id: "org_acme", platform: "google-cloud", display_name: project.display_name, roles: ["runtime", "secret-store"], interface: "api", authorization: "workload-identity", authorization_reference: `workload-identity://${account.email}`,
      capabilities: ["runtime.listServices", "runtime.inspectSecretBindings", "runtime.deployCandidate", "runtime.shiftTraffic", "runtime.rollback", "secretStore.getVersion", "secretStore.testConsumerAccess", "secretStore.disableVersion", "secretStore.destroyVersion"], allowed_resources: [...project.services.map((service) => service.reference), `projects/${project.project_id}/secrets`], http: null, playbook_id: null, playbook_version_id: null, status: "setup-required", authenticated_at: null, authorization_expires_at: null, last_validated_at: null, region: project.services[0].region, created_at: timestamp, updated_at: timestamp, archived_at: null, revision: 0,
    }
    store.connections.push(connection)
    for (const service of project.services) store.runtimeResources.push({ connection_id: connection.id, ...service, endpoint: null, identity: service.runtime_identity, environment_name: "Production", production: true, secret_bindings: [] })
    onboarding.session.connection_id = connection.id
    return json(response, 201, { connection, grant_command: `gcloud iam service-accounts add-iam-policy-binding ${account.email} --project=${project.project_id} --member=serviceAccount:firekey-broker@firekey-demo.iam.gserviceaccount.com --role=roles/iam.serviceAccountTokenCreator` })
  }
  const googleCloudVerifyMatch = path.match(/^\/google-cloud\/onboarding\/([a-z0-9_-]+)\/connection\/verify$/)
  if (request.method === "POST" && googleCloudVerifyMatch) {
    const input = await body(request)
    const onboarding = googleCloudOnboardings.get(googleCloudVerifyMatch[1])
    const connection = item(store.connections, onboarding?.session.connection_id)
    if (!connection) return json(response, 409, { code: "conflict", message: "Google Cloud connection has not been prepared" })
    if (connection.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: "Google Cloud connection changed; reload and try again" })
    const timestamp = new Date().toISOString()
    Object.assign(connection, { status: "ready", authenticated_at: timestamp, last_validated_at: timestamp, updated_at: timestamp, revision: connection.revision + 1 })
    return json(response, 200, connection)
  }
  if (request.method === "POST" && path === "/github/onboarding") {
    const timestamp = new Date()
    const expires = new Date(timestamp.getTime() + 15 * 60 * 1000).toISOString()
    const id = `github_${randomUUID().replaceAll("-", "").slice(0, 16)}`
    const state = `mock-github-state-${randomBytes(24).toString("hex")}`
    const session = { id, organisation_id: "org_acme", subject: "user_chigozie", state_hash: "0".repeat(64), verifier_hash: "1".repeat(64), status: "pending", installation_id: null, installation: null, repositories: [], created_at: timestamp.toISOString(), expires_at: expires, completed_at: null }
    githubOnboardings.set(id, { session, state, repositories: [] })
    return json(response, 201, {
      session,
      state,
      pkce_verifier: "mock-pkce-verifier-abcdefghijklmnopqrstuvwxyz-1234567890",
      installation_url: "http://127.0.0.1:5173/?github=callback&installation_id=123&setup_action=install",
      authorization_url: `http://127.0.0.1:5173/?github=callback&code=mock-github-code&state=${encodeURIComponent(state)}`,
    })
  }
  const githubDiscoveryMatch = path.match(/^\/github\/onboarding\/([a-z0-9_-]+)\/discover$/)
  if (request.method === "POST" && githubDiscoveryMatch) {
    const onboarding = githubOnboardings.get(githubDiscoveryMatch[1])
    if (!onboarding) return json(response, 404, { code: "not-found", message: "GitHub connection session was not found" })
    const input = await body(request)
    if (input.state !== onboarding.state || !input.pkce_verifier || !input.code || input.installation_id !== 123) return json(response, 422, { code: "validation-error", message: "GitHub callback is incomplete" })
    const timestamp = new Date().toISOString()
    const repositories = [
      { repository_id: 456, full_name: "acme/store-workers", private: true, default_branch: "main", secret_scanning: "enabled" },
      { repository_id: 457, full_name: "acme/billing-services", private: true, default_branch: "main", secret_scanning: "enabled" },
    ]
    const installation = { installation_id: 123, organisation_id: "org_acme", account_id: 44, account_login: "acme", account_type: "Organization", repository_selection: "selected", permissions: { secret_scanning_alerts: "read" }, events: ["secret_scanning_alert"], webhook_verified_at: timestamp, repositories_ready: true, active: true, deleted: false, ready: true, created_at: timestamp, updated_at: timestamp }
    onboarding.session = { ...onboarding.session, status: "discovered", installation_id: 123, installation, repositories }
    onboarding.repositories = repositories
    return json(response, 200, { session: onboarding.session, installation, repositories })
  }
  const githubCompletionMatch = path.match(/^\/github\/onboarding\/([a-z0-9_-]+)\/complete$/)
  if (request.method === "POST" && githubCompletionMatch) {
    const onboarding = githubOnboardings.get(githubCompletionMatch[1])
    if (!onboarding?.session.installation) return json(response, 409, { code: "conflict", message: "GitHub repositories have not been discovered" })
    const timestamp = new Date().toISOString()
    onboarding.session = { ...onboarding.session, status: "complete", completed_at: timestamp }
    const repositories = onboarding.repositories.map((entry) => ({ ...entry, installation_id: 123, organisation_id: "org_acme", updated_at: timestamp }))
    return json(response, 200, { session: onboarding.session, installation: onboarding.session.installation, repositories })
  }
  const listRoutes = new Map([
    ["/overview", store.overview],
    ["/inventory/connections", store.connections],
    ["/inventory/applications", store.applications],
    ["/inventory/environments", store.environments],
    ["/inventory/services", store.services],
    ["/inventory/credentials", store.credentials],
    ["/incidents", store.incidents],
    ["/runs", store.runs],
    ["/approvals", store.approvals],
    ["/playbooks", store.playbooks],
    ["/agents", store.agents],
    ["/audit", store.audits],
    ["/notifications", store.notifications],
  ])

  if (request.method === "GET" && path === "/notifications/topics") {
    return json(response, 200, notificationTopicsForRole(store.profile.role))
  }
  if (request.method === "GET" && path === "/notifications/endpoints") {
    return json(response, 200, store.notificationEndpoints.filter((endpoint) => endpoint.provider === "resend" && endpoint.principal_id === store.profile.id).map(emailEndpoint))
  }
  if (request.method === "POST" && path === "/notifications/endpoints") {
    const input = await body(request)
    const availableTopics = notificationTopicsForRole(store.profile.role)
    const selectedTopics = Array.isArray(input.topics) ? availableTopics.filter((topic) => input.topics.includes(topic.id)) : []
    const validEmail = typeof input.email_address === "string" && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(input.email_address.trim())
    if (!input.id || !validEmail || !input.topics?.length || selectedTopics.length !== new Set(input.topics).size) {
      return json(response, 422, { code: "validation-error", message: "Notification destination is incomplete" })
    }
    if (item(store.notificationEndpoints, input.id)) return json(response, 409, { code: "conflict", message: "Notification destination already exists" })
    const timestamp = new Date().toISOString()
    const email = input.email_address.trim().toLowerCase()
    const endpoint = { id: input.id, organisation_id: "org_acme", principal_id: store.profile.id, display_name: email, channel: "email", provider: "resend", auth_reference: store.notificationSecrets[0].reference, event_kinds: [...new Set(selectedTopics.flatMap((topic) => topic.event_kinds))], recipients: [email], sender: "alerts@firekey.example", enabled: true, created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.notificationEndpoints.push(endpoint)
    audit("notification.endpoint.created", `notification-endpoints/${endpoint.id}`, endpoint.revision)
    return json(response, 201, emailEndpoint(endpoint))
  }
  const notificationEndpointStateMatch = path.match(/^\/notifications\/endpoints\/([a-z0-9_-]+)\/state$/)
  if (request.method === "POST" && notificationEndpointStateMatch) {
    const endpoint = item(store.notificationEndpoints, notificationEndpointStateMatch[1])
    if (!endpoint || endpoint.principal_id !== store.profile.id) return json(response, 404, { code: "not-found", message: "Notification destination not found" })
    const input = await body(request)
    if (endpoint.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${endpoint.revision}; reload and try again` })
    if (typeof input.enabled !== "boolean") return json(response, 422, { code: "validation-error", message: "Notification destination state is required" })
    endpoint.enabled = input.enabled
    endpoint.updated_at = new Date().toISOString()
    endpoint.revision += 1
    audit("notification.endpoint.updated", `notification-endpoints/${endpoint.id}`, endpoint.revision)
    return json(response, 200, emailEndpoint(endpoint))
  }

  if (request.method === "GET" && path === "/settings/profile") {
    return json(response, 200, store.profile)
  }
  if (request.method === "PATCH" && path === "/settings/profile") {
    const input = await body(request)
    const name = typeof input.display_name === "string" ? input.display_name.trim() : ""
    if (!name) return json(response, 422, { code: "validation-error", message: "Name is required" })
    if (store.profile.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${store.profile.revision}; reload and try again` })
    store.profile.display_name = name
    store.profile.revision += 1
    const current = item(store.team, store.profile.id)
    if (current) {
      current.display_name = name
      current.updated_at = new Date().toISOString()
      current.revision += 1
    }
    audit("profile.updated", `profiles/${store.profile.id}`, store.profile.revision)
    return json(response, 200, store.profile)
  }
  if (request.method === "GET" && path === "/settings/team") {
    return json(response, 200, store.team.filter((member) => member.status !== "disabled"))
  }
  if (request.method === "POST" && path === "/settings/team/invitations") {
    const input = await body(request)
    const email = typeof input.email === "string" ? input.email.trim().toLowerCase() : ""
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || !["viewer", "operator", "administrator"].includes(input.role)) {
      return json(response, 422, { code: "validation-error", message: "Member email and role are required" })
    }
    if (store.team.some((member) => member.email === email && member.status !== "disabled")) {
      return json(response, 409, { code: "conflict", message: "This email already belongs to the team" })
    }
    const timestamp = new Date().toISOString()
    const member = { id: `invitation_${createHash("sha256").update(email).digest("hex").slice(0, 32)}`, organisation_id: "org_acme", display_name: null, email, connected_via: null, role: input.role, status: "pending", created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.team.push(member)
    audit("team.invited", `team/${member.id}`, member.revision)
    return json(response, 201, member)
  }
  const teamMemberMatch = path.match(/^\/settings\/team\/members\/([a-z0-9_-]+)$/)
  if (request.method === "PATCH" && teamMemberMatch) {
    const member = item(store.team, teamMemberMatch[1])
    if (!member || member.status === "pending") return json(response, 404, { code: "not-found", message: "Team member not found" })
    if (member.id === store.profile.id) return json(response, 409, { code: "conflict", message: "Change your own access from another administrator account" })
    const input = await body(request)
    if (member.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${member.revision}; reload and try again` })
    if (!["viewer", "operator", "administrator"].includes(input.role) || typeof input.enabled !== "boolean") return json(response, 422, { code: "validation-error", message: "Member role and status are required" })
    member.role = input.role
    member.status = input.enabled ? "active" : "disabled"
    member.updated_at = new Date().toISOString()
    member.revision += 1
    audit("team.member-updated", `team/${member.id}`, member.revision)
    return json(response, 200, member)
  }
  const teamInvitationMatch = path.match(/^\/settings\/team\/invitations\/([a-z0-9_-]+)\/cancel$/)
  if (request.method === "POST" && teamInvitationMatch) {
    const member = item(store.team, teamInvitationMatch[1])
    if (!member || member.status !== "pending") return json(response, 404, { code: "not-found", message: "Pending invitation not found" })
    const input = await body(request)
    if (member.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${member.revision}; reload and try again` })
    member.status = "disabled"
    member.updated_at = new Date().toISOString()
    member.revision += 1
    audit("team.invitation-cancelled", `team/${member.id}`, member.revision)
    return json(response, 200, member)
  }

  if (request.method === "GET" && path === "/inventory/graph") {
    const credentials = active(store.credentials)
    const services = active(store.services)
    const credentialIds = new Set(credentials.map((entry) => entry.id))
    const serviceIds = new Set(services.map((entry) => entry.id))
    return json(response, 200, { credentials, services, bindings: store.bindings.filter((entry) => credentialIds.has(entry.credential_id) && serviceIds.has(entry.service_id)) })
  }
  if (request.method === "GET" && listRoutes.has(path)) {
    const value = listRoutes.get(path)
    return json(response, 200, Array.isArray(value) ? active(value) : value)
  }

  const controlDetailMatch = path.match(/^\/inventory\/credentials\/([a-z0-9_-]+)\/controls\/([a-z0-9_-]+)$/)
  if (request.method === "GET" && controlDetailMatch) {
    const controls = store.controlVersions.find((entry) => entry.credential_id === controlDetailMatch[1] && entry.id === controlDetailMatch[2])
    return controls ? json(response, 200, controls) : json(response, 404, { code: "not-found", message: "Credential controls not found" })
  }

  const controlUpdateMatch = path.match(/^\/inventory\/credentials\/([a-z0-9_-]+)\/controls$/)
  if (request.method === "POST" && controlUpdateMatch) {
    const input = await body(request)
    const credential = item(store.credentials, controlUpdateMatch[1])
    if (!credential || credential.archived_at) return json(response, 404, { code: "not-found", message: "Credential not found" })
    if (credential.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: `Revision changed from ${input.expected_revision} to ${credential.revision}; reload and try again` })
    if (!input.version_id || !validControlPreferences(input.controls)) return json(response, 422, { code: "validation-error", message: "Automatic rotation controls are invalid" })
    if (input.controls.exposure_sources.some((source) => {
      const connection = item(store.connections, source.connection_id)
      return !connection?.roles.includes("incident") || connection.status !== "ready" || !connection.allowed_resources.includes(source.resource)
    })) return json(response, 409, { code: "conflict", message: "Exposure source is not available" })
    if (store.controlVersions.some((entry) => entry.credential_id === credential.id && entry.id === input.version_id)) return json(response, 409, { code: "conflict", message: `Control version ${input.version_id} already exists` })
    const versions = store.controlVersions.filter((entry) => entry.credential_id === credential.id)
    const previous = versions.find((entry) => entry.id === credential.control_version)
    if (!previous || !validControlDefinition(previous.definition)) return json(response, 409, { code: "conflict", message: "Current credential controls are unavailable" })
    const timestamp = new Date().toISOString()
    const definition = applyControlPreferences(previous.definition, input.controls)
    const controls = { id: input.version_id, organisation_id: credential.organisation_id, credential_id: credential.id, number: Math.max(0, ...versions.map((entry) => entry.number)) + 1, definition, digest: createHash("sha256").update(JSON.stringify(definition)).digest("hex"), created_by: "actor_chigozie", created_at: timestamp }
    credential.control_version = controls.id
    credential.updated_at = timestamp
    credential.revision += 1
    store.controlVersions.push(controls)
    audit("credential.controls.updated", `inventory/credentials/${credential.id}`, credential.revision)
    return json(response, 201, { credential, controls })
  }

  const providerCredentialsMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/credential-metadata$/)
  if (request.method === "GET" && providerCredentialsMatch) {
    const connection = item(store.connections, providerCredentialsMatch[1])
    if (!connection || connection.archived_at) return json(response, 404, { code: "not-found", message: "Connection not found" })
    if (!connection.roles.includes("provider") || connection.interface !== "api") return json(response, 409, { code: "conflict", message: "Credential discovery requires an API provider connection" })
    if (connection.status !== "ready") return json(response, 409, { code: "conflict", message: "Provider connection is not ready" })
    if (!connection.capabilities.includes("provider.listCredentialMetadata")) return json(response, 409, { code: "conflict", message: "Provider connection cannot list credential metadata" })
    return json(response, 200, store.providerCredentials.filter((entry) => entry.connection_id === connection.id).map(({ connection_id: _connectionId, ...entry }) => entry))
  }

  const resolveCredentialMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/resolve-credential$/)
  if (request.method === "POST" && resolveCredentialMatch) {
    const connection = item(store.connections, resolveCredentialMatch[1])
    const input = await body(request)
    if (!connection || connection.archived_at) return json(response, 404, { code: "not-found", message: "Connection not found" })
    const secretStore = item(store.connections, input.secret_store_connection_id)
    if (!secretStore?.roles.includes("secret-store") || !secretStore.allowed_resources.some((boundary) => input.secret_reference === boundary || input.secret_reference?.startsWith(`${boundary.replace(/\/$/, "")}/`))) return json(response, 409, { code: "conflict", message: "Credential secret reference escapes the secret store" })
    const resolved = store.credentialImports.find((entry) => entry.connection_id === connection.id && entry.secret_reference === input.secret_reference)
    const metadata = resolved && store.providerCredentials.find((entry) => entry.connection_id === connection.id && entry.provider_id === resolved.provider_id && entry.disabled !== true)
    if (!metadata) return json(response, 409, { code: "conflict", message: "This secret is not managed by the selected provider connection" })
    const { connection_id: _connectionId, ...credential } = metadata
    return json(response, 200, credential)
  }

  const runtimeResourcesMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/runtime-resources$/)
  if (request.method === "GET" && runtimeResourcesMatch) {
    const connection = item(store.connections, runtimeResourcesMatch[1])
    if (!connection || connection.archived_at) return json(response, 404, { code: "not-found", message: "Connection not found" })
    if (!connection.roles.includes("runtime") || connection.interface !== "api" || connection.status !== "ready") return json(response, 409, { code: "conflict", message: "Runtime discovery requires a ready API runtime connection" })
    if (!connection.capabilities.includes("runtime.listServices")) return json(response, 409, { code: "conflict", message: "Runtime connection cannot list services" })
    return json(response, 200, store.runtimeResources.filter((entry) => entry.connection_id === connection.id).map(({ connection_id: _connectionId, ...entry }) => entry))
  }

  const secretResourcesMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/secret-resources$/)
  if (request.method === "GET" && secretResourcesMatch) {
    const connection = item(store.connections, secretResourcesMatch[1])
    if (!connection || connection.archived_at) return json(response, 404, { code: "not-found", message: "Connection not found" })
    if (!connection.roles.includes("secret-store") || connection.interface !== "api" || connection.status !== "ready") return json(response, 409, { code: "conflict", message: "Secret discovery requires a ready secret-store connection" })
    const resources = [...new Set([
      ...store.credentials.filter((entry) => entry.secret_store_connection_id === connection.id).map((entry) => entry.secret_resource),
      ...store.credentialImports.map((entry) => entry.secret_resource),
    ])]
    return json(response, 200, resources.map((reference) => ({ reference, display_name: reference.split("/").at(-1) })))
  }

  const secretVersionsMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/secret-versions$/)
  if (request.method === "GET" && secretVersionsMatch) {
    const connection = item(store.connections, secretVersionsMatch[1])
    const secret = url.searchParams.get("secret")
    if (!connection || connection.archived_at) return json(response, 404, { code: "not-found", message: "Connection not found" })
    if (!secret || !connection.allowed_resources.some((boundary) => secret === boundary || secret.startsWith(`${boundary.replace(/\/$/, "")}/`))) return json(response, 409, { code: "conflict", message: "Secret resource escapes the connection boundary" })
    const references = [
      ...store.generations.map((entry) => entry.secret_reference),
      ...store.credentialImports.map((entry) => entry.secret_reference),
    ].filter((reference) => reference?.startsWith(`${secret}/versions/`))
    return json(response, 200, [...new Set(references)].map((reference) => ({ reference, state: "ENABLED", created_at: new Date().toISOString() })))
  }

  const inventoryMatch = path.match(/^\/inventory\/(connections|applications|environments|services|credentials)\/([a-z0-9_-]+)(\/archive)?$/)
  if (inventoryMatch) {
    const [, collectionName, resourceId, archivePath] = inventoryMatch
    const collection = store[collectionName]
    const current = item(collection, resourceId)
    if (!current) return json(response, 404, { code: "not-found", message: `${collectionName.slice(0, -1)} not found` })
    if (request.method === "GET" && !archivePath) return json(response, 200, current)
    const input = await body(request)
    if (request.method === "PATCH" && !archivePath) {
      const fields = {
        connections: ["display_name", "capabilities", "allowed_resources", "region"],
        applications: ["display_name", "repository_ids"],
        environments: ["display_name", "production", "region"],
        services: ["display_name", "runtime_connection_id", "telemetry_connection_ids", "runtime_resource", "endpoint", "repository", "identity"],
        credentials: ["display_name"],
      }[collectionName]
      if (collectionName === "connections") {
        const candidate = Object.assign({}, current, Object.fromEntries(fields.filter((field) => Object.hasOwn(input, field)).map((field) => [field, input[field]])))
        if (candidate.roles.includes("secret-store") && active(store.credentials).some((entry) => entry.secret_store_connection_id === candidate.id && !candidate.allowed_resources.some((boundary) => entry.secret_reference === boundary || entry.secret_reference.startsWith(`${boundary.replace(/\/$/, "")}/`)))) return json(response, 409, { code: "conflict", message: "Connection scope no longer covers an active secret" })
        if (candidate.roles.includes("runtime") && active(store.services).some((entry) => entry.runtime_connection_id === candidate.id && !candidate.allowed_resources.some((boundary) => entry.runtime_resource === boundary || entry.runtime_resource.startsWith(`${boundary.replace(/\/$/, "")}/`)))) return json(response, 409, { code: "conflict", message: "Connection scope no longer covers an active runtime" })
        if (candidate.interface === "browser" && candidate.playbook_version_id) {
          const version = item(store.playbookVersions, candidate.playbook_version_id)
          if (!version || version.definition.allowed_domains.some((domain) => !candidate.allowed_resources.some((boundary) => domain === boundary || (boundary.startsWith("*.") && domain.endsWith(boundary.slice(1)))))) return json(response, 409, { code: "conflict", message: "Connection domains no longer cover its playbook" })
        }
      }
      if (collectionName === "services") {
        const candidate = Object.assign({}, current, Object.fromEntries(fields.filter((field) => Object.hasOwn(input, field)).map((field) => [field, input[field]])))
        const runtime = item(store.connections, candidate.runtime_connection_id)
        const telemetry = candidate.telemetry_connection_ids.map((id) => item(store.connections, id))
        const covered = runtime?.allowed_resources.some((boundary) => candidate.runtime_resource === boundary || candidate.runtime_resource.startsWith(`${boundary.replace(/\/$/, "")}/`))
        if (!runtime?.roles.includes("runtime") || !covered || telemetry.some((entry) => !entry?.roles.includes("telemetry"))) return json(response, 409, { code: "conflict", message: "Service runtime or telemetry connection is invalid" })
      }
      if (!revise(response, current, input, fields)) return
      audit(`${collectionName.slice(0, -1)}.updated`, `inventory/${resourceId}`, current.revision)
      return json(response, 200, current)
    }
    if (request.method === "POST" && archivePath) {
      const blocked = collectionName === "credentials" ? current.consumer_ids.length > 0
        : collectionName === "services" ? store.bindings.some((entry) => entry.service_id === resourceId)
          : collectionName === "environments" ? active(store.services).some((entry) => entry.environment_id === resourceId)
            : collectionName === "applications" ? active(store.environments).some((entry) => entry.application_id === resourceId) || active(store.services).some((entry) => entry.application_id === resourceId)
              : active(store.credentials).some((entry) => [entry.connection_id, entry.secret_store_connection_id].includes(resourceId)) || active(store.services).some((entry) => entry.runtime_connection_id === resourceId || entry.telemetry_connection_ids.includes(resourceId))
      if (blocked && !input.cascade) return json(response, 409, { code: "conflict", message: `${collectionName.slice(0, -1)} is still used by active inventory` })
      if (input.expected_revision !== current.revision) return json(response, 409, { code: "conflict", message: `Expected revision ${input.expected_revision}, found ${current.revision}` })
      if (collectionName === "credentials" && input.cascade) {
        const timestamp = new Date().toISOString()
        const relatedRuns = store.runs.filter((run) => run.credential_id === resourceId)
        const relatedRunIds = new Set(relatedRuns.map((run) => run.id))
        for (const run of relatedRuns) if (["pending", "running", "paused", "recovering", "cleanup-required"].includes(run.status)) {
          run.status = "cancelled"
          run.lease = null
          run.fencing_token += 1
          run.failure = null
          run.recovery_id = null
          run.recovery_stage = null
          run.recovery_mode = null
          run.recovery_failure = null
          run.recovery_evidence_ids = []
          run.updated_at = timestamp
          run.revision += 1
        }
        for (const approval of store.approvals) if (relatedRunIds.has(approval.run_id) && approval.decision === "pending") {
          approval.decision = "cancelled"
          approval.approver_id = "actor_chigozie"
          approval.decided_at = timestamp
          approval.revision += 1
        }
        for (const incident of store.incidents) if (incident.credential_id === resourceId && !["resolved", "dismissed"].includes(incident.status)) {
          incident.status = "dismissed"
          incident.dismissal_reason = "Credential removed from FireKey."
          incident.updated_at = timestamp
          incident.revision += 1
        }
      }
      if (input.cascade) cascadeInventory(collectionName, resourceId)
      if (collectionName === "connections") current.status = "disabled"
      const result = archive(response, current, input)
      if (collectionName === "credentials" && current.archived_at) store.overview.credentials = active(store.credentials).length
      if (collectionName === "credentials" && current.archived_at) refreshOverview()
      if (current.archived_at) audit(`${collectionName.slice(0, -1)}.archived`, `inventory/${resourceId}`, current.revision)
      return result
    }
  }

  const playbookRootMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)(\/archive)?$/)
  if (playbookRootMatch) {
    const playbook = item(store.playbooks, playbookRootMatch[1])
    if (!playbook) return json(response, 404, { code: "not-found", message: "Playbook not found" })
    if (request.method === "GET" && !playbookRootMatch[2]) return json(response, 200, { playbook, active_version: item(store.playbookVersions, playbook.active_version_id) ?? null, latest_version: item(store.playbookVersions, playbook.latest_version_id) ?? null })
    const input = await body(request)
    if (request.method === "PATCH" && !playbookRootMatch[2]) {
      if (!revise(response, playbook, input, ["name"])) return
      audit("playbook.updated", `playbooks/${playbook.id}`, playbook.revision)
      return json(response, 200, playbook)
    }
    if (request.method === "POST" && playbookRootMatch[2]) {
      const attached = active(store.connections).filter((entry) => entry.playbook_id === playbook.id)
      if (attached.length && !input.cascade) return json(response, 409, { code: "conflict", message: "Playbook is still attached to an active connection" })
      if (input.cascade) for (const connection of attached) {
        connection.playbook_id = null
        connection.playbook_version_id = null
        connection.status = "setup-required"
        connection.updated_at = new Date().toISOString()
        connection.revision += 1
      }
      archive(response, playbook, input)
      if (playbook.archived_at) audit("playbook.archived", `playbooks/${playbook.id}`, playbook.revision)
      return
    }
  }

  if (request.method === "POST" && path === "/inventory/connections") {
    const connection = await body(request)
    if (!connection?.id || connection.organisation_id !== "org_acme" || !Array.isArray(connection.roles) || !connection.roles.length) return json(response, 422, { code: "validation-error", message: "Connection identity, organization, and role are required" })
    if (item(store.connections, connection.id)) return json(response, 409, { code: "conflict", message: `Connection ${connection.id} already exists` })
    if (connection.interface === "browser" && (connection.roles.length !== 1 || connection.roles[0] !== "provider" || connection.authorization !== "browser-session" || connection.status !== "setup-required")) return json(response, 409, { code: "conflict", message: "Browser connections start as provider-only setup-required connections" })
    if (connection.interface === "api" && connection.roles.includes("provider") && (!validProviderAdapter(connection.http) || !connection.capabilities.includes("provider.testCredential"))) return json(response, 422, { code: "validation-error", message: "API provider connections require a valid typed adapter" })
    if (connection.interface === "api" && (connection.roles.includes("provider") || connection.roles.includes("runtime") || connection.roles.includes("secret-store"))) {
      if (connection.status !== "setup-required" || !connection.authorization_reference) return json(response, 409, { code: "conflict", message: "API connections must be validated before becoming ready" })
      const timestamp = new Date().toISOString()
      connection.status = "ready"
      connection.authenticated_at = timestamp
      connection.last_validated_at = timestamp
      connection.updated_at = timestamp
    }
    store.connections.push(connection)
    return json(response, 201, connection)
  }

  if (request.method === "POST" && path === "/inventory/applications") {
    const application = await body(request)
    if (!application?.id || application.organisation_id !== "org_acme") return json(response, 422, { code: "validation-error", message: "Application identity is required" })
    if (item(store.applications, application.id)) return json(response, 409, { code: "conflict", message: `Application ${application.id} already exists` })
    store.applications.push(application)
    return json(response, 201, application)
  }

  if (request.method === "POST" && path === "/inventory/application-setups") {
    const input = await body(request)
    const runtime = item(store.connections, input.runtime_connection_id)
    const resource = store.runtimeResources.find((entry) => entry.connection_id === input.runtime_connection_id && entry.reference === input.runtime_resource)
    const environmentName = resource?.environment_name ?? input.environment_name
    if (!input.application_id || !input.environment_id || !input.service_id) return json(response, 422, { code: "validation-error", message: "Application setup identity is required" })
    if (!runtime?.roles.includes("runtime") || runtime.interface !== "api" || runtime.status !== "ready" || !resource) return json(response, 409, { code: "conflict", message: "Runtime service is no longer available" })
    if (!environmentName) return json(response, 409, { code: "conflict", message: "Runtime service environment is unavailable" })
    const timestamp = new Date().toISOString()
    const application = { id: input.application_id, organisation_id: "org_acme", display_name: resource.display_name, repository_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 }
    const environment = { id: input.environment_id, organisation_id: "org_acme", application_id: application.id, display_name: environmentName, production: resource.production ?? environmentName.toLowerCase() === "production", region: resource.region, created_at: timestamp, updated_at: timestamp, revision: 0 }
    const service = { id: input.service_id, organisation_id: "org_acme", application_id: application.id, environment_id: environment.id, runtime_connection_id: runtime.id, telemetry_connection_ids: [], runtime_resource: resource.reference, display_name: resource.display_name, endpoint: resource.endpoint, repository: null, identity: resource.identity, created_at: timestamp, updated_at: timestamp, revision: 0 }
    if (item(store.applications, application.id) || item(store.environments, environment.id) || item(store.services, service.id)) return json(response, 409, { code: "conflict", message: "Application setup resource already exists" })
    store.applications.push(application)
    store.environments.push(environment)
    store.services.push(service)
    return json(response, 201, input)
  }

  if (request.method === "POST" && path === "/inventory/environments") {
    const environment = await body(request)
    if (!environment?.id || environment.organisation_id !== "org_acme" || !item(store.applications, environment.application_id)) return json(response, 409, { code: "conflict", message: "Environment requires an application in this organization" })
    store.environments.push(environment)
    return json(response, 201, environment)
  }

  if (request.method === "POST" && path === "/inventory/services") {
    const input = await body(request)
    const environment = item(store.environments, input.environment_id)
    const runtime = item(store.connections, input.runtime_connection_id)
    const resource = store.runtimeResources.find((entry) => entry.connection_id === input.runtime_connection_id && entry.reference === input.runtime_resource)
    if (!input?.id || !environment || environment.application_id !== input.application_id) return json(response, 409, { code: "conflict", message: "Service application and environment do not match" })
    if (!runtime?.roles.includes("runtime") || runtime.interface !== "api" || runtime.status !== "ready" || !resource) return json(response, 409, { code: "conflict", message: "Runtime service is no longer available" })
    const timestamp = new Date().toISOString()
    const service = { id: input.id, organisation_id: "org_acme", application_id: input.application_id, environment_id: input.environment_id, runtime_connection_id: runtime.id, telemetry_connection_ids: [], runtime_resource: resource.reference, display_name: resource.display_name, endpoint: resource.endpoint, repository: null, identity: resource.identity, created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.services.push(service)
    return json(response, 201, service)
  }

  const walkthroughStartMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/walkthroughs$/)
  if (request.method === "POST" && walkthroughStartMatch) {
    const input = await body(request)
    if (!input.source_id || !["video/mp4", "video/webm", "video/quicktime"].includes(input.content_type) || !Number.isInteger(input.size) || input.size <= 0 || !input.crc32c) return json(response, 422, { code: "validation-error", message: "Video upload metadata is incomplete" })
    if (item(store.playbookSources, input.source_id)) return json(response, 409, { code: "conflict", message: `Source ${input.source_id} already exists` })
    const timestamp = new Date().toISOString()
    const source = { id: input.source_id, organisation_id: "org_acme", playbook_id: walkthroughStartMatch[1], kind: "video", object_name: `organisations/org_acme/playbooks/${walkthroughStartMatch[1]}/walkthroughs/${input.source_id}/video`, resource: `gs://mock-walkthroughs/${input.source_id}`, content_type: input.content_type, size: input.size, crc32c: input.crc32c, status: "uploading", operation: null, analysis: null, created_by: "actor_chigozie", created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.playbookSources.push(source)
    return json(response, 201, { source, upload_url: `http://127.0.0.1:${port}${organisationRoot}/playbooks/${walkthroughStartMatch[1]}/walkthroughs/${input.source_id}/upload` })
  }

  const walkthroughUploadMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/walkthroughs\/([a-z0-9_-]+)\/upload$/)
  if (request.method === "PUT" && walkthroughUploadMatch) {
    const source = item(store.playbookSources, walkthroughUploadMatch[2])
    if (!source || source.playbook_id !== walkthroughUploadMatch[1] || source.status !== "uploading") return json(response, 409, { code: "conflict", message: "Video upload is not available" })
    let uploadedSize = 0
    for await (const chunk of request) uploadedSize += chunk.length
    if (uploadedSize <= 0) return json(response, 422, { code: "validation-error", message: "Video upload is empty" })
    walkthroughUploads.add(source.id)
    return json(response, 200, { uploaded: true })
  }

  const walkthroughCompleteMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/walkthroughs\/([a-z0-9_-]+)\/complete$/)
  if (request.method === "POST" && walkthroughCompleteMatch) {
    const source = item(store.playbookSources, walkthroughCompleteMatch[2])
    if (!source || source.playbook_id !== walkthroughCompleteMatch[1] || !walkthroughUploads.has(source.id)) return json(response, 409, { code: "conflict", message: "Video upload is incomplete" })
    const timestamp = new Date().toISOString()
    source.resource = `${source.resource}#1`
    source.status = "ready"
    source.operation = "operations/mock-video-analysis"
    source.analysis = { source_id: source.id, transcript: [{ start_seconds: 0, end_seconds: 5, text: "Open credential settings, create and capture the replacement, then revoke the previous credential." }], screen_text: [{ start_seconds: 0, end_seconds: 5, text: "Credentials Create Revoke" }], shots: [{ start_seconds: 0, end_seconds: 5 }], redaction_count: 0, processor: "google-video-intelligence", created_at: timestamp }
    source.updated_at = timestamp
    source.revision += 1
    return json(response, 200, source)
  }

  const videoReferenceMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/walkthroughs\/video-references$/)
  if (request.method === "POST" && videoReferenceMatch) {
    const input = await body(request)
    if (!input.source_id || !/^(gs:\/\/|https:\/\/(?:storage\.googleapis\.com\/|[^/]+\.storage\.googleapis\.com\/))/.test(String(input.resource ?? ""))) return json(response, 422, { code: "validation-error", message: "Video link must identify a Cloud Storage object" })
    if (item(store.playbookSources, input.source_id)) return json(response, 409, { code: "conflict", message: `Source ${input.source_id} already exists` })
    const timestamp = new Date().toISOString()
    const objectName = `organisations/org_acme/playbooks/${videoReferenceMatch[1]}/walkthroughs/${input.source_id}/video`
    const source = { id: input.source_id, organisation_id: "org_acme", playbook_id: videoReferenceMatch[1], kind: "video", object_name: objectName, resource: `gs://mock-walkthroughs/${objectName}#1`, content_type: "video/mp4", size: 120, crc32c: "ImIEBA==", status: "ready", operation: "operations/mock-video-analysis", analysis: { source_id: input.source_id, transcript: [{ start_seconds: 0, end_seconds: 5, text: "Open credential settings, create and capture the replacement, then revoke the previous credential." }], screen_text: [{ start_seconds: 0, end_seconds: 5, text: "Credentials Create Revoke" }], shots: [{ start_seconds: 0, end_seconds: 5 }], redaction_count: 0, processor: "google-video-intelligence", created_at: timestamp }, created_by: "actor_chigozie", created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.playbookSources.push(source)
    return json(response, 201, source)
  }

  const walkthroughGetMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/walkthroughs\/([a-z0-9_-]+)$/)
  if (request.method === "GET" && walkthroughGetMatch) {
    const source = item(store.playbookSources, walkthroughGetMatch[2])
    if (!source || source.playbook_id !== walkthroughGetMatch[1]) return json(response, 404, { code: "not-found", message: "Playbook source was not found" })
    return json(response, 200, source)
  }

  const sourceMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/walkthroughs\/references$/)
  if (request.method === "POST" && sourceMatch) {
    const input = await body(request)
    if (!input.source_id || !["text", "link", "video"].includes(input.kind) || !String(input.content ?? "").trim()) return json(response, 422, { code: "validation-error", message: "Playbook source is incomplete" })
    if (item(store.playbookSources, input.source_id)) return json(response, 409, { code: "conflict", message: `Source ${input.source_id} already exists` })
    let resource
    if (input.kind === "text") resource = `sha256:${createHash("sha256").update(input.content).digest("hex")}`
    else {
      let parsed
      try { parsed = new URL(input.resource_url) } catch { return json(response, 422, { code: "validation-error", message: "Source URL is invalid" }) }
      if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) return json(response, 422, { code: "validation-error", message: "Source URLs must be credential-free HTTPS URLs without query or fragment" })
      resource = parsed.toString()
    }
    const sanitised = sanitiseSource(input.content)
    const timestamp = new Date().toISOString()
    const source = { id: input.source_id, organisation_id: "org_acme", playbook_id: sourceMatch[1], kind: input.kind, resource, content_type: input.kind === "text" ? "text/plain" : "text/uri-list", size: Buffer.byteLength(input.content), status: "ready", analysis: { source_id: input.source_id, transcript: [{ start_seconds: 0, end_seconds: 0, text: sanitised.text }], screen_text: [], shots: [], redaction_count: sanitised.redactions, processor: "firekey-source-sanitizer", created_at: timestamp }, created_by: "actor_chigozie", created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.playbookSources.push(source)
    return json(response, 201, source)
  }

  const playbookDraftMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/draft$/)
  if (request.method === "POST" && playbookDraftMatch) {
    const input = await body(request)
    if (!input.objective || !Array.isArray(input.source_ids) || !input.source_ids.length || input.source_ids.some((id) => item(store.playbookSources, id)?.playbook_id !== playbookDraftMatch[1] || item(store.playbookSources, id)?.status !== "ready")) return json(response, 409, { code: "conflict", message: "Playbook draft requires ready source evidence" })
    const definition = generatedPlaybook(input.objective)
    return json(response, 200, { definition, agent: { succeeded: true, output: { playbook_draft: definition }, evidence_ids: input.source_ids } })
  }

  const playbookBuildMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/build$/)
  if (request.method === "POST" && playbookBuildMatch) {
    const input = await body(request)
    if (!input.version_id || !Array.isArray(input.source_ids) || !input.source_ids.length || input.source_ids.some((id) => item(store.playbookSources, id)?.playbook_id !== playbookBuildMatch[1])) return json(response, 409, { code: "conflict", message: "Playbook build requires ready source evidence" })
    let definition
    try { definition = JSON.parse(input.objective) } catch { return json(response, 422, { code: "validation-error", message: "Playbook build objective is invalid" }) }
    if (!definition?.name || !definition.platform || !definition.login_url_pattern || !Array.isArray(definition.allowed_domains) || !definition.allowed_domains.length || !Array.isArray(definition.steps) || definition.steps.filter((step) => step.effect === "create-credential" && step.stage === "create" && step.tool === "browser.secure-capture" && step.secure_field && step.selectors?.[0]?.value !== step.secure_field.selector?.value).length !== 1 || definition.steps.filter((step) => step.effect === "revoke-credential" && step.stage === "revoke" && step.tool === "browser.revokeCredential").length !== 1) return json(response, 422, { code: "validation-error", message: "Playbook Builder Agent returned an invalid definition" })
    const timestamp = new Date().toISOString()
    let playbook = item(store.playbooks, playbookBuildMatch[1])
    if (!playbook) {
      playbook = { id: playbookBuildMatch[1], organisation_id: "org_acme", name: definition.name, platform: definition.platform, latest_version: 1, latest_version_id: input.version_id, active_version_id: null, created_at: timestamp, updated_at: timestamp, revision: 0 }
      store.playbooks.push(playbook)
    } else {
      if (playbook.archived_at) return json(response, 409, { code: "conflict", message: "Archived Playbooks cannot receive new versions" })
      if (playbook.name !== definition.name || playbook.platform !== definition.platform) return json(response, 409, { code: "conflict", message: "Playbook name and platform must match its root" })
      playbook.latest_version += 1
      playbook.latest_version_id = input.version_id
      playbook.updated_at = timestamp
      playbook.revision += 1
    }
    const version = { id: input.version_id, organisation_id: "org_acme", playbook_id: playbook.id, number: playbook.latest_version, definition, source_ids: input.source_ids, state: "draft", created_at: timestamp }
    store.playbookVersions.push(version)
    return json(response, 201, { playbook, version, agent: { succeeded: true, output: { playbook_draft: definition }, evidence_ids: input.source_ids } })
  }

  const playbookVersionMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/versions$/)
  if (request.method === "POST" && playbookVersionMatch) {
    const input = await body(request)
    const definition = input.definition
    if (!input.version_id || !definition?.name || !definition.platform || !definition.login_url_pattern || !Array.isArray(definition.allowed_domains) || !definition.allowed_domains.length || !Array.isArray(definition.steps)) return json(response, 422, { code: "validation-error", message: "Playbook definition is incomplete" })
    if (!Array.isArray(input.source_ids) || !input.source_ids.length || input.source_ids.some((id) => item(store.playbookSources, id)?.playbook_id !== playbookVersionMatch[1])) return json(response, 409, { code: "conflict", message: "Playbook version requires registered source evidence" })
    if (definition.steps.filter((step) => step.effect === "create-credential" && step.stage === "create" && step.tool === "browser.secure-capture" && step.secure_field && step.selectors?.[0]?.value !== step.secure_field.selector?.value).length !== 1 || definition.steps.filter((step) => step.effect === "revoke-credential" && step.stage === "revoke" && step.tool === "browser.revokeCredential").length !== 1) return json(response, 422, { code: "validation-error", message: "Playbook requires one secure create action and one protected revoke action" })
    const timestamp = new Date().toISOString()
    let playbook = item(store.playbooks, playbookVersionMatch[1])
    if (!playbook) {
      playbook = { id: playbookVersionMatch[1], organisation_id: "org_acme", name: definition.name, platform: definition.platform, latest_version: 1, latest_version_id: input.version_id, active_version_id: null, created_at: timestamp, updated_at: timestamp, revision: 0 }
      store.playbooks.push(playbook)
    } else {
      if (playbook.archived_at) return json(response, 409, { code: "conflict", message: "Archived Playbooks cannot receive new versions" })
      if (playbook.name !== definition.name || playbook.platform !== definition.platform) return json(response, 409, { code: "conflict", message: "Playbook name and platform must match its root" })
      playbook.latest_version += 1
      playbook.latest_version_id = input.version_id
      playbook.updated_at = timestamp
      playbook.revision += 1
    }
    const version = { id: input.version_id, organisation_id: "org_acme", playbook_id: playbook.id, number: playbook.latest_version, definition, source_ids: input.source_ids ?? [], state: "draft", created_at: timestamp }
    store.playbookVersions.push(version)
    return json(response, 201, { playbook, version })
  }

  const publishMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/versions\/([a-z0-9_-]+)\/publish$/)
  if (request.method === "POST" && publishMatch) {
    const playbook = item(store.playbooks, publishMatch[1])
    const version = item(store.playbookVersions, publishMatch[2])
    if (!playbook || !version || version.playbook_id !== playbook.id) return json(response, 404, { code: "not-found", message: "Playbook version not found" })
    if (playbook.archived_at || version.state !== "draft") return json(response, 409, { code: "conflict", message: "Only a draft version of an active Playbook can be published" })
    const previous = item(store.playbookVersions, playbook.active_version_id)
    if (previous) previous.state = "superseded"
    version.state = "published"
    version.published_at = new Date().toISOString()
    version.published_by = "actor_chigozie"
    playbook.active_version_id = version.id
    playbook.updated_at = version.published_at
    playbook.revision += 1
    return json(response, 200, version)
  }

  const attachMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/versions\/([a-z0-9_-]+)\/attach$/)
  if (request.method === "POST" && attachMatch) {
    const input = await body(request)
    const playbook = item(store.playbooks, attachMatch[1])
    const connection = item(store.connections, input.connection_id)
    if (!playbook || playbook.archived_at || playbook.active_version_id !== attachMatch[2]) return json(response, 409, { code: "conflict", message: "Only a published active Playbook version can be attached" })
    if (!connection || connection.interface !== "browser" || connection.platform !== playbook.platform || connection.revision !== input.expected_revision) return json(response, 409, { code: "conflict", message: "Browser connection, platform, or revision does not match the Playbook" })
    connection.playbook_id = playbook.id
    connection.playbook_version_id = attachMatch[2]
    connection.revision += 1
    connection.updated_at = new Date().toISOString()
    return json(response, 200, connection)
  }

  const setupMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/setup$/)
  if (request.method === "POST" && setupMatch) {
    const connection = item(store.connections, setupMatch[1])
    if (!connection || connection.interface !== "browser" || !connection.playbook_version_id) return json(response, 409, { code: "conflict", message: "Attach a published Playbook before opening the browser" })
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    const token = randomBytes(32).toString("base64url")
    const session = { id: `setup_${connection.id}`, connection_id: connection.id, token_hash: createHash("sha256").update(token).digest("hex"), secret_container: "projects/firekey-control/secrets/firekey-browser-session-org_acme", revision: 0, expires_at: expiresAt }
    const previous = item(store.setups, session.id)
    if (previous) Object.assign(previous, session)
    else store.setups.push(session)
    return json(response, 201, { session: { id: session.id, revision: session.revision, expires_at: session.expires_at }, token, gateway_url: "http://127.0.0.1:5173/browser/setup", expires_at: expiresAt })
  }

  const completeSetupMatch = path.match(/^\/inventory\/setups\/([a-z0-9_-]+)\/complete$/)
  if (request.method === "POST" && completeSetupMatch) {
    const input = await body(request)
    const session = item(store.setups, completeSetupMatch[1])
    if (!session || session.revision !== input.expected_revision || new Date(session.expires_at).getTime() <= Date.now()) return json(response, 409, { code: "conflict", message: "Browser setup session is stale or expired" })
    const actual = createHash("sha256").update(input.token ?? "").digest()
    const expected = Buffer.from(session.token_hash, "hex")
    if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) return json(response, 403, { code: "forbidden", message: "Browser setup token is invalid" })
    const connection = item(store.connections, session.connection_id)
    const timestamp = new Date().toISOString()
    connection.authorization_reference = `${session.secret_container}/versions/1`
    connection.status = "ready"
    connection.authenticated_at = timestamp
    connection.authorization_expires_at = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString()
    connection.last_validated_at = timestamp
    connection.updated_at = timestamp
    connection.revision += 1
    session.revision += 1
    return json(response, 200, { session: { id: session.id, revision: session.revision, expires_at: session.expires_at }, connection, resumed_run_ids: [] })
  }

  if (request.method === "POST" && path === "/inventory/credentials") {
    let input
    try {
      input = await body(request)
    } catch {
      return json(response, 422, { code: "validation-error", message: "Request body must be valid JSON" })
    }
    const credential = input.credential
    const generation = input.generation
    const consumer = input.consumer
    const preferences = input.controls
    if (!credential || !generation || !consumer || !validControlPreferences(preferences)) {
      return json(response, 422, { code: "validation-error", message: "Credential, generation, runtime service, and controls are required" })
    }
    if ([credential, generation].some((entry) => entry.organisation_id !== "org_acme")) {
      return json(response, 409, { code: "conflict", message: "Inventory relationship crosses organisation boundaries" })
    }
    if (generation.credential_id !== credential.id || credential.active_generation_id !== generation.id) {
      return json(response, 409, { code: "conflict", message: "Imported generation lineage is inconsistent" })
    }
    if (credential.secret_reference !== generation.secret_reference) return json(response, 409, { code: "conflict", message: "Credential and active generation secret references differ" })
    if (credential.secret_reference.split("/versions/")[0] !== credential.secret_resource) return json(response, 409, { code: "conflict", message: "Credential secret version does not belong to its resource" })
    if (!credential.secret_reference.includes("/versions/")) return json(response, 409, { code: "conflict", message: "Credential secret reference must identify one immutable version" })
    const runtime = item(store.connections, consumer.runtime_connection_id)
    const resource = store.runtimeResources.find((entry) => entry.connection_id === consumer.runtime_connection_id && entry.reference === consumer.runtime_resource)
    if (!runtime?.roles.includes("runtime") || runtime.interface !== "api" || runtime.status !== "ready" || !resource) {
      return json(response, 409, { code: "conflict", message: "Runtime service is no longer available" })
    }
    const matches = store.services.filter((entry) => !entry.archived_at && entry.runtime_connection_id === consumer.runtime_connection_id && entry.runtime_resource === consumer.runtime_resource)
    if (matches.length > 1) return json(response, 409, { code: "conflict", message: "Runtime service mapping is ambiguous" })
    const timestamp = new Date().toISOString()
    let application = null
    let environment = null
    let service = matches[0]
    if (service) {
      if (consumer.service_id !== service.id || consumer.application_id !== service.application_id || consumer.environment_id !== service.environment_id) return json(response, 409, { code: "conflict", message: "Runtime service selection changed before import" })
    } else {
      const environmentName = resource.environment_name ?? consumer.environment_name
      if (!environmentName) return json(response, 409, { code: "conflict", message: "Runtime service environment is unavailable" })
      if (item(store.applications, consumer.application_id) || item(store.environments, consumer.environment_id) || item(store.services, consumer.service_id)) return json(response, 409, { code: "conflict", message: "Runtime service inventory already exists" })
      application = { id: consumer.application_id, organisation_id: "org_acme", display_name: resource.display_name, repository_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 }
      environment = { id: consumer.environment_id, organisation_id: "org_acme", application_id: application.id, display_name: environmentName, production: resource.production ?? environmentName.toLowerCase() === "production", region: resource.region, created_at: timestamp, updated_at: timestamp, revision: 0 }
      service = { id: consumer.service_id, organisation_id: "org_acme", application_id: application.id, environment_id: environment.id, runtime_connection_id: runtime.id, telemetry_connection_ids: [], runtime_resource: resource.reference, display_name: resource.display_name, endpoint: resource.endpoint, repository: null, identity: resource.identity, created_at: timestamp, updated_at: timestamp, revision: 0 }
    }
    const binding = { id: consumer.binding_id, organisation_id: "org_acme", credential_id: credential.id, service_id: service.id, environment_id: service.environment_id, runtime_connection_id: service.runtime_connection_id, runtime_resource: service.runtime_resource, runtime_secret_name: consumer.runtime_secret_name, runtime_container_name: consumer.runtime_container_name ?? null, secret_reference: generation.secret_reference, current_generation_id: generation.id, target_generation_id: null, verification_report_id: null, required: true, revision: 0 }
    const bindings = [binding]
    if (credential.consumer_ids.length !== 1 || credential.consumer_ids[0] !== service.id) {
      return json(response, 409, { code: "conflict", message: "Credential consumer and runtime service must match" })
    }
    if (item(store.credentials, credential.id) || item(store.generations, generation.id) || item(store.bindings, binding.id) || store.controlVersions.some((entry) => entry.credential_id === credential.id && entry.id === credential.control_version)) {
      return json(response, 409, { code: "conflict", message: `Credential ${credential.id} is already imported` })
    }
    const management = item(store.connections, credential.connection_id)
    const secretStore = item(store.connections, credential.secret_store_connection_id)
    if (!management?.roles.includes("provider") || management.platform !== credential.provider || !secretStore?.roles.includes("secret-store")) {
      return json(response, 404, { code: "not-found", message: "Credential connection is missing" })
    }
    if (preferences.exposure_sources.some((source) => {
      const connection = item(store.connections, source.connection_id)
      return !connection?.roles.includes("incident") || connection.status !== "ready" || !connection.allowed_resources.includes(source.resource)
    })) return json(response, 409, { code: "conflict", message: "Exposure source is not available" })
    if (management.interface === "browser" && (!management.playbook_version_id || management.status !== "ready")) return json(response, 409, { code: "conflict", message: "Browser credential connection is not ready" })
    const verifiedImport = store.credentialImports.find((entry) => entry.connection_id === credential.connection_id && entry.provider_id === credential.provider_id && entry.secret_resource === credential.secret_resource && entry.secret_reference === credential.secret_reference)
    if (!verifiedImport) return json(response, 409, { code: "conflict", message: "Stored secret does not authenticate as the selected provider credential" })
    if (!binding.runtime_secret_name) return json(response, 422, { code: "validation-error", message: "Runtime binding is required" })
    if (!runtimeBindingMatches(resource, credential.secret_resource, credential.secret_reference, binding.runtime_secret_name, binding.runtime_container_name)) return json(response, 409, { code: "conflict", message: "Runtime service does not use the selected secret version" })

    const template = store.controlVersions[0]?.definition
    if (!validControlDefinition(template)) return json(response, 409, { code: "conflict", message: "Control compiler template is unavailable" })
    const definition = applyControlPreferences(template, preferences)
    const telemetryEnabled = Boolean(service.telemetry_connection_ids.length)
    definition.require_generation_telemetry = telemetryEnabled
    if (!telemetryEnabled) {
      definition.required_checks.verify = definition.required_checks.verify.filter((check) => check !== "telemetry-healthy")
      definition.required_checks.observe = definition.required_checks.observe.filter((check) => !["telemetry-healthy", "old-use-clear"].includes(check))
    }
    definition.probe_versions = {
      verify: bindings.flatMap((entry) => [`probe_${entry.id}_provider`, `probe_${entry.id}_credential`, `probe_${entry.id}_secret`, `probe_${entry.id}_runtime`, ...(service.telemetry_connection_ids.length ? [`probe_${entry.id}_telemetry`] : [])]),
      observe: bindings.flatMap((entry) => [`probe_${entry.id}_runtime_observe`, ...(service.telemetry_connection_ids.length ? [`probe_${entry.id}_telemetry_target`, `probe_${entry.id}_telemetry_old`] : [])]),
      revoke: [`probe_${credential.id}_provider_revoke`, `probe_${credential.id}_credential_reject`, `probe_${credential.id}_secret_enabled`],
    }
    const rollback = { mode: "rollback", actions: bindings.map((binding) => ({ tool: "runtime.rollback", operation: "rollback", parameters: { connection_id: binding.runtime_connection_id, service: binding.runtime_resource }, protected: false })), preserves_old_generation: true }
    definition.recovery = { deploy: rollback, verify: rollback, rollout: rollback, observe: rollback }
    const controls = { id: credential.control_version, organisation_id: credential.organisation_id, credential_id: credential.id, number: 1, definition, digest: createHash("sha256").update(JSON.stringify(definition)).digest("hex"), created_by: "actor_chigozie", created_at: credential.created_at }
    if (application && environment) {
      store.applications.push(application)
      store.environments.push(environment)
      store.services.push(service)
    }
    store.credentials.push(credential)
    store.generations.push(generation)
    store.bindings.push(...bindings)
    store.controlVersions.push(controls)
    store.overview.credentials = store.credentials.length
    return json(response, 201, credential)
  }

  if (request.method === "POST" && path === "/runs") {
    const input = await body(request)
    const credential = item(store.credentials, input.credential_id)
    if (!credential || credential.control_version !== input.control_version) return json(response, 409, { code: "conflict", message: "Credential and controls version do not match" })
    if (store.runs.some((run) => run.credential_id === credential.id && !["completed", "compensated", "failed", "cleanup-required"].includes(run.status))) return json(response, 409, { code: "conflict", message: "This credential already has an active rotation" })
    if (!String(input.reason ?? "").trim() || !["routine", "urgent", "emergency"].includes(input.urgency)) return json(response, 422, { code: "validation-error", message: "Rotation reason and urgency are required" })
    const timestamp = new Date().toISOString()
    const run = { id: `run_manual_${randomBytes(8).toString("hex")}`, organisation_id: "org_acme", credential_id: credential.id, trigger: { source: "manual", kind: "manual", event_id: input.event_id, actor_id: "actor_chigozie", reason: input.reason.trim(), urgency: input.urgency, received_at: input.received_at ?? timestamp }, control_version: input.control_version, stage: "trigger", status: "pending", lease: null, fencing_token: 0, browser_playbook_version: null, plan_id: null, plan_hash: null, current_generation_id: credential.active_generation_id, target_generation_id: null, deployments: [], failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.runs.unshift(run)
    refreshOverview()
    scheduleMockRotation(run)
    return json(response, 201, { run, step: { id: `step_${run.id}`, organisation_id: "org_acme", run_id: run.id, operation: "create", command_hash: createHash("sha256").update(run.id).digest("hex"), actor_id: "actor_chigozie", before_stage: null, after_stage: "trigger", before_status: null, after_status: "pending", revision: 0, proof: null, recorded_at: timestamp }, applied: true })
  }

  const runHistoryMatch = path.match(/^\/runs\/([a-z0-9_-]+)\/history$/)
  if (request.method === "GET" && runHistoryMatch) {
    const run = item(store.runs, runHistoryMatch[1])
    return run ? json(response, 200, runHistory(run)) : json(response, 404, { code: "not-found", message: "Run not found" })
  }

  const computerUseImageMatch = path.match(/^\/runs\/([a-z0-9_-]+)\/computer-use\/([a-z0-9_-]+)\/image$/)
  if (request.method === "GET" && computerUseImageMatch) {
    const run = item(store.runs, computerUseImageMatch[1])
    const activity = run && runComputerUse(run).find((entry) => entry.id === computerUseImageMatch[2] && entry.phase === "input")
    if (!activity) return json(response, 404, { code: "not-found", message: "Computer Use input not found" })
    response.writeHead(200, {
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "private, no-store",
      "Content-Security-Policy": "default-src 'none'",
      "Content-Type": "image/svg+xml; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
      "X-FireKey-Source": "mock-server",
    })
    return response.end(computerUseInputImage(activity.stage))
  }

  const runMatch = path.match(/^\/runs\/([a-z0-9_-]+)$/)
  if (request.method === "GET" && runMatch) {
    const run = item(store.runs, runMatch[1])
    return run ? json(response, 200, run) : json(response, 404, { code: "not-found", message: "Run not found" })
  }

  const incidentConfirmMatch = path.match(/^\/incidents\/([a-z0-9_-]+)\/confirm$/)
  if (request.method === "POST" && incidentConfirmMatch) {
    const incident = item(store.incidents, incidentConfirmMatch[1])
    if (!incident) return json(response, 404, { code: "not-found", message: "Incident not found" })
    const input = await body(request)
    if (input.expected_revision !== incident.revision) return json(response, 409, { code: "conflict", message: "Incident revision changed; reload before confirming" })
    if (!["correlating", "action-required"].includes(incident.status)) return json(response, 409, { code: "conflict", message: "Incident cannot be confirmed in its current state" })
    if (!incident.candidates.some((candidate) => candidate.credential_id === input.credential_id)) return json(response, 409, { code: "conflict", message: "Credential is not a correlated incident candidate" })
    incident.candidates = incident.candidates.map((candidate) => ({ ...candidate, confidence: candidate.credential_id === input.credential_id ? "verified" : candidate.confidence === "verified" ? "high" : candidate.confidence }))
    incident.credential_id = input.credential_id
    incident.status = "action-required"
    incident.updated_at = new Date().toISOString()
    incident.revision += 1
    refreshOverview()
    return json(response, 200, incident)
  }

  const incidentRotateMatch = path.match(/^\/incidents\/([a-z0-9_-]+)\/rotate$/)
  if (request.method === "POST" && incidentRotateMatch) {
    const incident = item(store.incidents, incidentRotateMatch[1])
    if (!incident) return json(response, 404, { code: "not-found", message: "Incident not found" })
    const input = await body(request)
    if (!["action-required", "rotation-started"].includes(incident.status)) return json(response, 409, { code: "conflict", message: "Incident has no confirmed credential for rotation" })
    const credential = item(store.credentials, incident.credential_id)
    if (!credential || credential.control_version !== input.control_version) return json(response, 409, { code: "conflict", message: "Incident credential and controls do not match" })
    if (incident.run_id) {
      const existing = item(store.runs, incident.run_id)
      return existing ? json(response, 200, { incident, run: existing, applied: false }) : json(response, 409, { code: "conflict", message: "Incident rotation is unavailable" })
    }
    if (store.runs.some((run) => run.credential_id === credential.id && !["completed", "compensated", "failed", "cleanup-required", "cancelled"].includes(run.status))) return json(response, 409, { code: "conflict", message: "This credential already has an active rotation" })
    if (!String(input.reason ?? "").trim() || !["routine", "urgent", "emergency"].includes(input.urgency)) return json(response, 422, { code: "validation-error", message: "Rotation reason and urgency are required" })
    const timestamp = new Date().toISOString()
    const provider = item(store.connections, credential.connection_id)
    const run = { id: `run_incident_${randomBytes(8).toString("hex")}`, organisation_id: "org_acme", credential_id: credential.id, trigger: { source: incident.source, kind: incident.kind, event_id: incident.event_id, actor_id: "actor_chigozie", reason: input.reason.trim(), urgency: input.urgency, received_at: input.received_at ?? timestamp }, control_version: input.control_version, stage: "trigger", status: "pending", lease: null, fencing_token: 0, browser_playbook_version: provider?.interface === "browser" ? provider.playbook_version_id : null, plan_id: null, plan_hash: null, current_generation_id: credential.active_generation_id, target_generation_id: null, deployments: [], failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.runs.unshift(run)
    incident.run_id = run.id
    incident.status = "rotation-started"
    incident.updated_at = timestamp
    incident.revision += 1
    refreshOverview()
    scheduleMockRotation(run)
    return json(response, 201, { incident, run, applied: true })
  }

  const incidentDismissMatch = path.match(/^\/incidents\/([a-z0-9_-]+)\/dismiss$/)
  if (request.method === "POST" && incidentDismissMatch) {
    const incident = item(store.incidents, incidentDismissMatch[1])
    if (!incident) return json(response, 404, { code: "not-found", message: "Incident not found" })
    const input = await body(request)
    if (input.expected_revision !== incident.revision) return json(response, 409, { code: "conflict", message: "Incident revision changed; reload before dismissing" })
    if (incident.status === "resolved") return json(response, 409, { code: "conflict", message: "Resolved incidents cannot be dismissed" })
    if (!String(input.reason ?? "").trim()) return json(response, 422, { code: "validation-error", message: "Dismissal reason is required" })
    incident.status = "dismissed"
    incident.dismissal_reason = input.reason.trim()
    incident.updated_at = new Date().toISOString()
    incident.revision += 1
    refreshOverview()
    return json(response, 200, incident)
  }

  const incidentMatch = path.match(/^\/incidents\/([a-z0-9_-]+)$/)
  if (request.method === "GET" && incidentMatch) {
    const incident = item(store.incidents, incidentMatch[1])
    return incident ? json(response, 200, incident) : json(response, 404, { code: "not-found", message: "Incident not found" })
  }

  const approvalEvidenceMatch = path.match(/^\/approvals\/([a-z0-9_-]+)\/evidence$/)
  if (request.method === "GET" && approvalEvidenceMatch) {
    const approval = item(store.approvals, approvalEvidenceMatch[1])
    if (!approval) return json(response, 404, { code: "not-found", message: "Approval not found" })
    return json(response, 200, { approval_id: approval.id, evidence_hash: approval.evidence_hash, kind: "verification", status: "passed", checks: ["provider-valid", "store-valid", "deployment-valid", "old-use-clear", "rollback-ready"], evidence_count: 5, recorded_at: new Date(Date.parse(approval.created_at) - 60_000).toISOString() })
  }

  const approvalMatch = path.match(/^\/approvals\/([a-z0-9_-]+)\/decision$/)
  if (request.method === "POST" && approvalMatch) {
    const approval = item(store.approvals, approvalMatch[1])
    if (!approval) return json(response, 404, { code: "not-found", message: "Approval not found" })
    const input = await body(request)
    if (input.expected_revision !== approval.revision) {
      return json(response, 409, { code: "conflict", message: "Approval revision changed; reload before deciding" })
    }
    if (approval.decision !== "pending") return json(response, 409, { code: "conflict", message: "Approval has already been decided" })
    if (Date.parse(approval.expires_at) <= Date.now()) return json(response, 409, { code: "approval-expired", message: "Approval has expired" })
    const accepted = ["approved", "rejected", "more-evidence", "extend-observation"]
    if (!accepted.includes(input.decision)) return json(response, 422, { code: "transition-rejected", message: "Unsupported approval decision" })

    const timestamp = new Date().toISOString()
    approval.decision = input.decision
    approval.approver_id = "actor_chigozie"
    approval.decided_at = timestamp
    approval.revision += 1
    const run = item(store.runs, approval.run_id)
    if (input.decision === "approved") {
      setTimeout(() => {
        approval.consumed_at = new Date().toISOString()
        approval.revision += 1
        if (run?.status === "paused" && run.stage === "approval") {
          moveRun(run, "revoke", "running")
          setTimeout(() => moveRun(run, "complete", "completed"), 250)
        }
      }, 50)
    } else if (input.decision === "rejected") {
      if (run?.status === "paused") {
        run.failure = { code: "approval-rejected", message: "Revocation approval was rejected.", retryable: false, evidence_ids: [] }
        moveRun(run, "approval", "failed")
      }
    } else if (run?.status === "paused") {
      moveRun(run, "observe", "running")
      setTimeout(() => {
        if (run.status !== "running") return
        moveRun(run, "approval", "paused")
        createMockApproval(run)
      }, 300)
    }
    refreshOverview()
    return json(response, 200, approval)
  }

  return json(response, 404, { code: "not-found", message: `Mock route ${request.method} ${path} is not implemented` })
}).listen(port, "127.0.0.1", () => {
  process.stdout.write(`FireKey mock API listening on http://127.0.0.1:${port}\n`)
})
