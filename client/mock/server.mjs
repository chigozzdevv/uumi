import { createServer } from "node:http"
import { createHash, randomBytes, timingSafeEqual } from "node:crypto"
import { createStore } from "./data.mjs"

const port = Number(process.env.FIREKEY_MOCK_PORT ?? 8787)
const organisationRoot = "/v1/organisations/org_acme"
const store = createStore()

function json(response, status, body) {
  response.writeHead(status, {
    "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
    "Access-Control-Allow-Methods": "GET, PATCH, POST, OPTIONS",
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

function active(items) {
  return items.filter((entry) => !entry.archived_at)
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
  store.overview.pending_approvals = store.approvals.filter((approval) => approval.decision === "pending").length
}

function moveRun(run, stage, status) {
  if (!run || ["completed", "compensated", "failed"].includes(run.status)) return
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

function scheduleMockRotation(run) {
  const transitions = [
    [150, "preflight"],
    [300, "plan"],
    [450, "create"],
    [600, "store"],
    [750, "deploy"],
    [900, "verify"],
    [1050, "rollout"],
    [1200, "observe"],
  ]
  for (const [delay, stage] of transitions) {
    setTimeout(() => {
      if (["pending", "running"].includes(run.status)) moveRun(run, stage, "running")
    }, delay)
  }
  setTimeout(() => {
    if (!["pending", "running"].includes(run.status)) return
    moveRun(run, "approval", "paused")
    createMockApproval(run)
  }, 1350)
}

function validControlDefinition(definition) {
  return Boolean(
    definition
    && controlStages.every((stage) => Array.isArray(definition.required_checks?.[stage]) && definition.required_checks[stage].length)
    && Array.isArray(definition.allowed_tools)
    && definition.allowed_tools.length
    && Array.isArray(definition.allowed_recovery_modes)
    && definition.allowed_recovery_modes.length
    && (!definition.require_generation_telemetry || definition.allowed_tools.includes("verification.run"))
    && (definition.protected_tools ?? []).every((tool) => definition.allowed_tools.includes(tool)),
  )
}

function validControlPreferences(preferences) {
  const supported = new Set(["expiry", "drift", "verified-exposure"])
  return Boolean(
    preferences
    && Array.isArray(preferences.automatic_triggers)
    && preferences.automatic_triggers.length
    && preferences.automatic_triggers.every((trigger) => supported.has(trigger))
    && preferences.rotate_before_expiry_seconds >= 300
    && preferences.maximum_observation_seconds >= 60,
  )
}

function applyControlPreferences(definition, preferences) {
  return {
    ...structuredClone(definition),
    automatic_triggers: [...preferences.automatic_triggers],
    emergency_triggers: preferences.automatic_triggers.includes("verified-exposure") ? ["verified-exposure"] : [],
    rotate_before_expiry_seconds: preferences.rotate_before_expiry_seconds,
    maximum_observation_seconds: preferences.maximum_observation_seconds,
  }
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

createServer(async (request, response) => {
  if (request.method === "OPTIONS") return json(response, 204, {})
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`)
  await new Promise((resolve) => setTimeout(resolve, 90))

  if (request.method === "GET" && url.pathname === "/health") return json(response, 200, { status: "ok", service: "firekey-mock" })
  if (!url.pathname.startsWith(organisationRoot)) return json(response, 404, { code: "not-found", message: "Mock route not found" })

  const path = url.pathname.slice(organisationRoot.length)
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
    const resources = [...new Set(store.credentials.filter((entry) => entry.secret_store_connection_id === connection.id).map((entry) => entry.secret_reference.split("/versions/")[0]))]
    return json(response, 200, resources.map((reference) => ({ reference, display_name: reference.split("/").at(-1) })))
  }

  const secretVersionsMatch = path.match(/^\/inventory\/connections\/([a-z0-9_-]+)\/secret-versions$/)
  if (request.method === "GET" && secretVersionsMatch) {
    const connection = item(store.connections, secretVersionsMatch[1])
    const secret = url.searchParams.get("secret")
    if (!connection || connection.archived_at) return json(response, 404, { code: "not-found", message: "Connection not found" })
    if (!secret || !connection.allowed_resources.some((boundary) => secret === boundary || secret.startsWith(`${boundary.replace(/\/$/, "")}/`))) return json(response, 409, { code: "conflict", message: "Secret resource escapes the connection boundary" })
    return json(response, 200, [{ reference: `${secret}/versions/1`, state: "ENABLED", created_at: new Date().toISOString() }])
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
        services: ["display_name", "runtime_connection_id", "telemetry_connection_ids", "runtime_resource", "verification", "repository", "identity"],
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
      if (input.cascade) cascadeInventory(collectionName, resourceId)
      if (collectionName === "connections") current.status = "disabled"
      const result = archive(response, current, input)
      if (collectionName === "credentials" && current.archived_at) store.overview.credentials = active(store.credentials).length
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
    if (connection.interface === "api" && connection.roles.includes("provider") && !connection.http) return json(response, 422, { code: "validation-error", message: "API provider connections require typed HTTP operations" })
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
    const { application, environment, service } = input
    const runtime = item(store.connections, service?.runtime_connection_id)
    const telemetry = (service?.telemetry_connection_ids ?? []).map((id) => item(store.connections, id))
    const runtimeCovered = runtime?.allowed_resources.some((boundary) => service.runtime_resource === boundary || service.runtime_resource.startsWith(`${boundary.replace(/\/$/, "")}/`))
    if (!application?.id || !environment?.id || !service?.id || [application, environment, service].some((value) => value.organisation_id !== "org_acme")) return json(response, 422, { code: "validation-error", message: "Application setup identity is required" })
    if (environment.application_id !== application.id || service.application_id !== application.id || service.environment_id !== environment.id) return json(response, 409, { code: "conflict", message: "Application setup relationships do not match" })
    if (!runtime?.roles.includes("runtime") || runtime.interface !== "api" || !runtimeCovered || telemetry.some((entry) => !entry?.roles.includes("telemetry") || entry.interface !== "api")) return json(response, 409, { code: "conflict", message: "Application runtime or telemetry connection is invalid" })
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
    const service = await body(request)
    const environment = item(store.environments, service.environment_id)
    const runtime = item(store.connections, service.runtime_connection_id)
    const telemetry = (service.telemetry_connection_ids ?? []).map((id) => item(store.connections, id))
    if (!service?.id || service.organisation_id !== "org_acme" || !environment || environment.application_id !== service.application_id) return json(response, 409, { code: "conflict", message: "Service application and environment do not match" })
    const runtimeCovered = runtime?.allowed_resources.some((boundary) => service.runtime_resource === boundary || service.runtime_resource.startsWith(`${boundary.replace(/\/$/, "")}/`))
    if (!runtime?.roles.includes("runtime") || runtime.interface !== "api" || !runtimeCovered || telemetry.some((entry) => !entry?.roles.includes("telemetry") || entry.interface !== "api")) return json(response, 409, { code: "conflict", message: "Service runtime or telemetry connection is invalid" })
    store.services.push(service)
    return json(response, 201, service)
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

  const playbookBuildMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/build$/)
  if (request.method === "POST" && playbookBuildMatch) {
    const input = await body(request)
    if (!input.version_id || !Array.isArray(input.source_ids) || !input.source_ids.length || input.source_ids.some((id) => item(store.playbookSources, id)?.playbook_id !== playbookBuildMatch[1])) return json(response, 409, { code: "conflict", message: "Playbook build requires ready source evidence" })
    let definition
    try { definition = JSON.parse(input.objective) } catch { return json(response, 422, { code: "validation-error", message: "Playbook build objective is invalid" }) }
    if (!definition?.name || !definition.platform || !definition.login_url_pattern || !Array.isArray(definition.allowed_domains) || !definition.allowed_domains.length || !Array.isArray(definition.steps) || definition.steps.filter((step) => step.effect === "create-credential" && step.stage === "create" && step.tool === "browser.secure-capture" && step.secure_field).length !== 1 || definition.steps.filter((step) => step.effect === "revoke-credential" && step.stage === "revoke").length !== 1) return json(response, 422, { code: "validation-error", message: "Playbook Builder Agent returned an invalid definition" })
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
    if (definition.steps.filter((step) => step.effect === "create-credential" && step.stage === "create" && step.tool === "browser.secure-capture" && step.secure_field).length !== 1 || definition.steps.filter((step) => step.effect === "revoke-credential" && step.stage === "revoke").length !== 1) return json(response, 422, { code: "validation-error", message: "Playbook requires one secure create action and one revoke action" })
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
    const input = await body(request)
    const connection = item(store.connections, setupMatch[1])
    if (!connection || connection.interface !== "browser" || !connection.playbook_version_id) return json(response, 409, { code: "conflict", message: "Attach a published Playbook before opening the browser" })
    if (!/^projects\/[a-z0-9-]+\/secrets\/[A-Za-z0-9_-]+$/.test(input.secret_container ?? "")) return json(response, 422, { code: "validation-error", message: "Browser session store is invalid" })
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000).toISOString()
    const token = randomBytes(32).toString("base64url")
    const session = { id: `setup_${connection.id}`, connection_id: connection.id, token_hash: createHash("sha256").update(token).digest("hex"), secret_container: input.secret_container, revision: 0, expires_at: expiresAt }
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
    const bindings = input.bindings
    const preferences = input.controls
    if (!credential || !generation || !Array.isArray(bindings) || !validControlPreferences(preferences)) {
      return json(response, 422, { code: "validation-error", message: "Credential, generation, bindings, and controls are required" })
    }
    if ([credential, generation, ...bindings].some((entry) => entry.organisation_id !== "org_acme")) {
      return json(response, 409, { code: "conflict", message: "Inventory relationship crosses organisation boundaries" })
    }
    if (generation.credential_id !== credential.id || credential.active_generation_id !== generation.id) {
      return json(response, 409, { code: "conflict", message: "Imported generation lineage is inconsistent" })
    }
    if (credential.secret_reference !== generation.secret_reference) return json(response, 409, { code: "conflict", message: "Credential and active generation secret references differ" })
    if (!credential.secret_reference.includes("/versions/")) return json(response, 409, { code: "conflict", message: "Credential secret reference must identify one immutable version" })
    const consumerIds = new Set(credential.consumer_ids)
    const bindingServices = new Set(bindings.map((entry) => entry.service_id))
    const consumersMatch = consumerIds.size === bindingServices.size && [...consumerIds].every((id) => bindingServices.has(id))
    const lineageMatches = bindings.every((entry) => entry.credential_id === credential.id && entry.current_generation_id === generation.id)
    if (!consumersMatch || !lineageMatches) {
      return json(response, 409, { code: "conflict", message: "Credential consumers and binding lineage must match exactly" })
    }
    if (item(store.credentials, credential.id) || item(store.generations, generation.id) || bindings.some((entry) => item(store.bindings, entry.id)) || store.controlVersions.some((entry) => entry.credential_id === credential.id && entry.id === credential.control_version)) {
      return json(response, 409, { code: "conflict", message: `Credential ${credential.id} is already imported` })
    }
    const management = item(store.connections, credential.connection_id)
    const secretStore = item(store.connections, credential.secret_store_connection_id)
    if (!management?.roles.includes("provider") || management.platform !== credential.provider || !secretStore?.roles.includes("secret-store") || bindings.some((entry) => !item(store.services, entry.service_id))) {
      return json(response, 404, { code: "not-found", message: "Credential connection or consumer service is missing" })
    }
    if (management.interface === "browser" && (!management.playbook_version_id || management.status !== "ready")) return json(response, 409, { code: "conflict", message: "Browser credential connection is not ready" })
    if (bindings.some((entry) => { const service = item(store.services, entry.service_id); return !service || !service.verification || entry.environment_id !== service.environment_id || entry.runtime_connection_id !== service.runtime_connection_id || entry.runtime_resource !== service.runtime_resource || entry.secret_reference !== generation.secret_reference })) return json(response, 409, { code: "conflict", message: "Credential binding does not match its consumer service, verification, or generation" })
    if (bindings.some((entry) => !entry.runtime_secret_name)) return json(response, 422, { code: "validation-error", message: "Runtime secret name is required for every binding" })

    const template = store.controlVersions[0]?.definition
    if (!validControlDefinition(template)) return json(response, 409, { code: "conflict", message: "Control compiler template is unavailable" })
    const definition = applyControlPreferences(template, preferences)
    definition.probe_versions = {
      verify: bindings.flatMap((binding) => [binding.verification_id, `probe_${binding.id}_provider`, `probe_${binding.id}_credential`, `probe_${binding.id}_secret`, `probe_${binding.id}_runtime`, `probe_${binding.id}_telemetry`]),
      observe: bindings.flatMap((binding) => [`probe_${binding.id}_runtime_observe`, `probe_${binding.id}_telemetry_target`, `probe_${binding.id}_telemetry_old`]),
      revoke: [`probe_${credential.id}_provider_revoke`, `probe_${credential.id}_credential_reject`, `probe_${credential.id}_secret_enabled`],
    }
    const rollback = { mode: "rollback", actions: bindings.map((binding) => ({ tool: "runtime.rollback", operation: "rollback", parameters: { connection_id: binding.runtime_connection_id, service: binding.runtime_resource }, protected: false })), preserves_old_generation: true }
    definition.recovery = { deploy: rollback, verify: rollback, rollout: rollback, observe: rollback }
    const controls = { id: credential.control_version, organisation_id: credential.organisation_id, credential_id: credential.id, number: 1, definition, digest: createHash("sha256").update(JSON.stringify(definition)).digest("hex"), created_by: "actor_chigozie", created_at: credential.created_at }
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
    const run = { id: `run_manual_${randomBytes(8).toString("hex")}`, organisation_id: "org_acme", credential_id: credential.id, trigger: { source: "manual", event_id: input.event_id, actor_id: "actor_chigozie", reason: input.reason.trim(), urgency: input.urgency, received_at: input.received_at ?? timestamp }, control_version: input.control_version, stage: "trigger", status: "pending", lease: null, fencing_token: 0, browser_playbook_version: null, plan_id: null, plan_hash: null, current_generation_id: credential.active_generation_id, target_generation_id: null, deployments: [], failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.runs.unshift(run)
    refreshOverview()
    scheduleMockRotation(run)
    return json(response, 201, { run, step: { id: `step_${run.id}`, organisation_id: "org_acme", run_id: run.id, operation: "create", command_hash: createHash("sha256").update(run.id).digest("hex"), actor_id: "actor_chigozie", before_stage: null, after_stage: "trigger", before_status: null, after_status: "pending", revision: 0, proof: null, recorded_at: timestamp }, applied: true })
  }

  const runMatch = path.match(/^\/runs\/([a-z0-9_-]+)$/)
  if (request.method === "GET" && runMatch) {
    const run = item(store.runs, runMatch[1])
    return run ? json(response, 200, run) : json(response, 404, { code: "not-found", message: "Run not found" })
  }

  const incidentMatch = path.match(/^\/incidents\/([a-z0-9_-]+)$/)
  if (request.method === "GET" && incidentMatch) {
    const incident = item(store.incidents, incidentMatch[1])
    return incident ? json(response, 200, incident) : json(response, 404, { code: "not-found", message: "Incident not found" })
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
    const accepted = ["approved", "rejected", "more-evidence", "extend-observation"]
    if (!accepted.includes(input.decision)) return json(response, 422, { code: "transition-rejected", message: "Unsupported approval decision" })

    const timestamp = new Date().toISOString()
    approval.decision = input.decision
    approval.approver_id = "actor_chigozie"
    approval.decided_at = timestamp
    approval.revision += 1
    const run = item(store.runs, approval.run_id)
    if (input.decision === "approved") {
      approval.consumed_at = timestamp
      if (run?.status === "paused" && run.stage === "approval") {
        moveRun(run, "revoke", "running")
        setTimeout(() => moveRun(run, "complete", "completed"), 250)
      }
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
