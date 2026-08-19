import { createServer } from "node:http"
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
    if (!item(store.connections, credential.connection_id) || bindings.some((entry) => !item(store.services, entry.service_id))) {
      return json(response, 404, { code: "not-found", message: "Credential connection or consumer service is missing" })
    }

    store.credentials.push(credential)
    store.generations.push(generation)
    store.bindings.push(...bindings)
    store.overview.credentials = store.credentials.length
    return json(response, 201, credential)
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
