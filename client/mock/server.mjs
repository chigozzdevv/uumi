import { createServer } from "node:http"
import { createHash, randomBytes, timingSafeEqual } from "node:crypto"
import { createStore } from "./data.mjs"

const port = Number(process.env.FIREKEY_MOCK_PORT ?? 8787)
const organisationRoot = "/v1/organisations/org_acme"
const store = createStore()

function json(response, status, body) {
  response.writeHead(status, {
    "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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
    ["/incidents", store.incidents],
    ["/runs", store.runs],
    ["/approvals", store.approvals],
    ["/policies", store.policies],
    ["/playbooks", store.playbooks],
    ["/agents", store.agents],
    ["/audit", store.audits],
    ["/notifications", store.notifications],
  ])

  if (request.method === "GET" && path === "/inventory/graph") {
    return json(response, 200, { credentials: store.credentials, services: store.services, bindings: store.bindings })
  }
  if (request.method === "GET" && listRoutes.has(path)) return json(response, 200, listRoutes.get(path))

  if (request.method === "POST" && path === "/policies") {
    const input = await body(request)
    if (!input?.id || !String(input.name ?? "").trim()) return json(response, 422, { code: "validation-error", message: "Policy identity and name are required" })
    if (item(store.policies, input.id)) return json(response, 409, { code: "conflict", message: `Policy ${input.id} already exists` })
    const timestamp = new Date().toISOString()
    const policy = { id: input.id, organisation_id: "org_acme", name: input.name.trim(), latest_version: 0, active_version_id: null, created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.policies.push(policy)
    return json(response, 201, policy)
  }

  const policyVersionMatch = path.match(/^\/policies\/([a-z0-9_-]+)\/versions$/)
  if (request.method === "POST" && policyVersionMatch) {
    const input = await body(request)
    const policy = item(store.policies, policyVersionMatch[1])
    const definition = input.definition
    const stages = ["trigger", "preflight", "plan", "create", "store", "deploy", "verify", "rollout", "observe", "approval", "revoke", "complete"]
    if (!policy) return json(response, 404, { code: "not-found", message: "Policy not found" })
    if (!input.id || !definition || stages.some((stage) => !Array.isArray(definition.required_checks?.[stage])) || !definition.allowed_tools?.length || !definition.allowed_recovery_modes?.length) return json(response, 422, { code: "validation-error", message: "Policy definition must cover every rotation stage" })
    if (definition.require_generation_telemetry && !definition.allowed_tools.includes("verification.run")) return json(response, 422, { code: "validation-error", message: "Generation telemetry requires verification.run" })
    if ((definition.protected_tools ?? []).some((tool) => !definition.allowed_tools.includes(tool))) return json(response, 422, { code: "validation-error", message: "Protected tools must also be allowed" })
    policy.latest_version += 1
    policy.updated_at = new Date().toISOString()
    policy.revision += 1
    const version = { id: input.id, organisation_id: "org_acme", policy_id: policy.id, number: policy.latest_version, definition, state: "draft", created_by: "actor_chigozie", created_at: policy.updated_at, approved_by: null, approved_at: null }
    store.policyVersions.push(version)
    return json(response, 201, version)
  }

  const policyActivateMatch = path.match(/^\/policies\/([a-z0-9_-]+)\/versions\/([a-z0-9_-]+)\/activate$/)
  if (request.method === "POST" && policyActivateMatch) {
    const policy = item(store.policies, policyActivateMatch[1])
    const version = item(store.policyVersions, policyActivateMatch[2])
    if (!policy || !version || version.policy_id !== policy.id) return json(response, 404, { code: "not-found", message: "Policy version not found" })
    const timestamp = new Date().toISOString()
    version.state = "active"
    version.approved_by = "actor_chigozie"
    version.approved_at = timestamp
    policy.active_version_id = version.id
    policy.updated_at = timestamp
    policy.automatic_triggers = version.definition.automatic_triggers
    policy.protected_operations = version.definition.protected_tools
    return json(response, 200, version)
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
    if (!definition?.name || !definition.platform || !definition.login_url_pattern || !Array.isArray(definition.allowed_domains) || !definition.allowed_domains.length || !Array.isArray(definition.steps) || !definition.steps.some((step) => step.stage === "create" && step.secure_field) || !definition.steps.some((step) => step.stage === "revoke")) return json(response, 422, { code: "validation-error", message: "Playbook Builder Agent returned an invalid definition" })
    const timestamp = new Date().toISOString()
    let playbook = item(store.playbooks, playbookBuildMatch[1])
    if (!playbook) {
      playbook = { id: playbookBuildMatch[1], organisation_id: "org_acme", name: definition.name, platform: definition.platform, latest_version: 1, active_version_id: null, created_at: timestamp, updated_at: timestamp, revision: 0 }
      store.playbooks.push(playbook)
    } else {
      playbook.latest_version += 1
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
    if (!definition.steps.some((step) => step.stage === "create" && step.secure_field) || !definition.steps.some((step) => step.stage === "revoke")) return json(response, 422, { code: "validation-error", message: "Playbook requires secure create and revoke actions" })
    const timestamp = new Date().toISOString()
    let playbook = item(store.playbooks, playbookVersionMatch[1])
    if (!playbook) {
      playbook = { id: playbookVersionMatch[1], organisation_id: "org_acme", name: definition.name, platform: definition.platform, latest_version: 1, active_version_id: null, created_at: timestamp, updated_at: timestamp, revision: 0 }
      store.playbooks.push(playbook)
    } else {
      playbook.latest_version += 1
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
    version.state = "published"
    version.published_at = new Date().toISOString()
    version.published_by = "actor_chigozie"
    playbook.active_version_id = version.id
    return json(response, 200, version)
  }

  const attachMatch = path.match(/^\/playbooks\/([a-z0-9_-]+)\/versions\/([a-z0-9_-]+)\/attach$/)
  if (request.method === "POST" && attachMatch) {
    const input = await body(request)
    const playbook = item(store.playbooks, attachMatch[1])
    const connection = item(store.connections, input.connection_id)
    if (!playbook || playbook.active_version_id !== attachMatch[2]) return json(response, 409, { code: "conflict", message: "Only a published Playbook version can be attached" })
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
    if (!credential || !generation || !Array.isArray(bindings)) {
      return json(response, 422, { code: "validation-error", message: "Credential, generation, and bindings are required" })
    }
    if ([credential, generation, ...bindings].some((entry) => entry.organisation_id !== "org_acme")) {
      return json(response, 409, { code: "conflict", message: "Inventory relationship crosses organisation boundaries" })
    }
    if (generation.credential_id !== credential.id || credential.active_generation_id !== generation.id) {
      return json(response, 409, { code: "conflict", message: "Imported generation lineage is inconsistent" })
    }
    if (credential.secret_reference !== generation.secret_reference) return json(response, 409, { code: "conflict", message: "Credential and active generation secret references differ" })
    const consumerIds = new Set(credential.consumer_ids)
    const bindingServices = new Set(bindings.map((entry) => entry.service_id))
    const consumersMatch = consumerIds.size === bindingServices.size && [...consumerIds].every((id) => bindingServices.has(id))
    const lineageMatches = bindings.every((entry) => entry.credential_id === credential.id && entry.current_generation_id === generation.id)
    if (!consumersMatch || !lineageMatches) {
      return json(response, 409, { code: "conflict", message: "Credential consumers and binding lineage must match exactly" })
    }
    if (item(store.credentials, credential.id) || item(store.generations, generation.id) || bindings.some((entry) => item(store.bindings, entry.id))) {
      return json(response, 409, { code: "conflict", message: `Credential ${credential.id} is already imported` })
    }
    const management = item(store.connections, credential.connection_id)
    const secretStore = item(store.connections, credential.secret_store_connection_id)
    if (!management?.roles.includes("provider") || management.platform !== credential.provider || !secretStore?.roles.includes("secret-store") || bindings.some((entry) => !item(store.services, entry.service_id))) {
      return json(response, 404, { code: "not-found", message: "Credential connection or consumer service is missing" })
    }
    if (management.interface === "browser" && (!management.playbook_version_id || management.status !== "ready")) return json(response, 409, { code: "conflict", message: "Browser credential connection is not ready" })
    if (bindings.some((entry) => { const service = item(store.services, entry.service_id); return !service || entry.environment_id !== service.environment_id || entry.runtime_connection_id !== service.runtime_connection_id || entry.runtime_resource !== service.runtime_resource || entry.secret_reference !== generation.secret_reference })) return json(response, 409, { code: "conflict", message: "Credential binding does not match its consumer service or generation" })
    if (bindings.some((entry) => !entry.runtime_secret_name)) return json(response, 422, { code: "validation-error", message: "Runtime secret name is required for every binding" })

    store.credentials.push(credential)
    store.generations.push(generation)
    store.bindings.push(...bindings)
    store.overview.credentials = store.credentials.length
    return json(response, 201, credential)
  }

  if (request.method === "POST" && path === "/runs") {
    const input = await body(request)
    const credential = item(store.credentials, input.credential_id)
    if (!credential || credential.policy_version !== input.policy_version) return json(response, 409, { code: "conflict", message: "Credential and active policy version do not match" })
    if (store.runs.some((run) => run.credential_id === credential.id && !["completed", "compensated", "failed", "cleanup-required"].includes(run.status))) return json(response, 409, { code: "conflict", message: "This credential already has an active rotation" })
    if (!String(input.reason ?? "").trim() || !["routine", "urgent", "emergency"].includes(input.urgency)) return json(response, 422, { code: "validation-error", message: "Rotation reason and urgency are required" })
    const timestamp = new Date().toISOString()
    const run = { id: `run_manual_${randomBytes(8).toString("hex")}`, organisation_id: "org_acme", credential_id: credential.id, trigger: { source: "manual", event_id: input.event_id, actor_id: "actor_chigozie", reason: input.reason.trim(), urgency: input.urgency, received_at: input.received_at ?? timestamp }, policy_version: input.policy_version, stage: "trigger", status: "pending", lease: null, fencing_token: 0, browser_playbook_version: null, plan_id: null, plan_hash: null, current_generation_id: credential.active_generation_id, target_generation_id: null, deployments: [], failure: null, recovery_id: null, recovery_stage: null, recovery_mode: null, recovery_failure: null, recovery_evidence_ids: [], created_at: timestamp, updated_at: timestamp, revision: 0 }
    store.runs.unshift(run)
    store.overview.rotations_in_progress += 1
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

    approval.decision = input.decision
    approval.approver_id = "actor_chigozie"
    approval.decided_at = new Date().toISOString()
    approval.revision += 1
    store.overview.pending_approvals = store.approvals.filter((entry) => entry.decision === "pending").length
    return json(response, 200, approval)
  }

  return json(response, 404, { code: "not-found", message: `Mock route ${request.method} ${path} is not implemented` })
}).listen(port, "127.0.0.1", () => {
  process.stdout.write(`FireKey mock API listening on http://127.0.0.1:${port}\n`)
})
