import assert from "node:assert/strict"
import { createStore } from "./data.mjs"

const store = createStore()
const ids = (items) => new Set(items.map((item) => item.id))
const credentialIds = ids(store.credentials)
const serviceIds = ids(store.services)
const environmentIds = ids(store.environments)
const connectionIds = ids(store.connections)
const generationIds = ids(store.generations)
const runIds = ids(store.runs)
const sourceIds = ids(store.playbookSources)

assert.equal(store.overview.credentials, store.credentials.length)
assert.equal(store.overview.pending_approvals, store.approvals.filter((item) => item.decision === "pending").length)
assert.equal(store.overview.rotations_in_progress, store.runs.filter((item) => ["pending", "running", "paused", "recovering", "cleanup-required"].includes(item.status)).length)
assert.equal(store.overview.failed_rotations, store.runs.filter((item) => item.status === "failed").length)
assert.equal(store.overview.open_incidents, store.incidents.filter((item) => !["resolved", "dismissed"].includes(item.status)).length)

for (const credential of store.credentials) {
  const management = store.connections.find((connection) => connection.id === credential.connection_id)
  const secretStore = store.connections.find((connection) => connection.id === credential.secret_store_connection_id)
  assert(management?.roles.includes("provider"), `${credential.id} requires a provider connection`)
  assert.equal(management.platform, credential.provider, `${credential.id} platform does not match its provider connection`)
  assert(secretStore?.roles.includes("secret-store"), `${credential.id} requires a secret-store connection`)
  assert(credential.secret_reference, `${credential.id} requires a secret reference`)
  assert.equal(store.generations.find((generation) => generation.id === credential.active_generation_id)?.secret_reference, credential.secret_reference, `${credential.id} secret reference differs from its active generation`)
  assert(secretStore.allowed_resources.some((boundary) => credential.secret_reference === boundary || credential.secret_reference.startsWith(`${boundary.replace(/\/$/, "")}/`)), `${credential.id} escapes its secret-store boundary`)
  assert(!("playbook_version" in credential), `${credential.id} must not assign a Playbook directly`)
  assert(generationIds.has(credential.active_generation_id), `${credential.id} references an unknown active generation`)
  for (const serviceId of credential.consumer_ids) {
    assert(serviceIds.has(serviceId), `${credential.id} references an unknown consumer`)
    assert(
      store.bindings.some((binding) => binding.credential_id === credential.id && binding.service_id === serviceId),
      `${credential.id} is missing a binding for ${serviceId}`,
    )
  }
}

for (const generation of store.generations) {
  assert(credentialIds.has(generation.credential_id), `${generation.id} references an unknown credential`)
  assert.equal(store.credentials.find((credential) => credential.id === generation.credential_id)?.active_generation_id, generation.id, `${generation.id} is not active for its credential`)
}

for (const binding of store.bindings) {
  assert(credentialIds.has(binding.credential_id), `${binding.id} references an unknown credential`)
  assert(serviceIds.has(binding.service_id), `${binding.id} references an unknown service`)
  assert(environmentIds.has(binding.environment_id), `${binding.id} references an unknown environment`)
  assert(connectionIds.has(binding.runtime_connection_id), `${binding.id} references an unknown runtime connection`)
  assert(binding.runtime_secret_name, `${binding.id} requires a runtime secret name`)
  const service = store.services.find((item) => item.id === binding.service_id)
  const generation = store.generations.find((item) => item.id === binding.current_generation_id)
  assert.equal(binding.environment_id, service.environment_id, `${binding.id} environment differs from its service`)
  assert.equal(binding.runtime_connection_id, service.runtime_connection_id, `${binding.id} runtime differs from its service`)
  assert.equal(binding.runtime_resource, service.runtime_resource, `${binding.id} resource differs from its service`)
  assert.equal(binding.secret_reference, generation.secret_reference, `${binding.id} secret reference differs from its generation`)
}

for (const service of store.services) {
  const runtime = store.connections.find((connection) => connection.id === service.runtime_connection_id)
  assert(runtime?.roles.includes("runtime"), `${service.id} requires a runtime connection`)
  assert(runtime.allowed_resources.some((boundary) => service.runtime_resource === boundary || service.runtime_resource.startsWith(`${boundary.replace(/\/$/, "")}/`)), `${service.id} escapes its runtime boundary`)
  for (const telemetryId of service.telemetry_connection_ids) assert(store.connections.find((connection) => connection.id === telemetryId)?.roles.includes("telemetry"), `${service.id} has an invalid telemetry connection`)
}

for (const connection of store.connections) {
  assert(connection.roles.length, `${connection.id} requires a role`)
  if (connection.interface === "browser") {
    assert.deepEqual(connection.roles, ["provider"], `${connection.id} browser access must be provider-only`)
    if (connection.status !== "setup-required") assert(connection.playbook_id && connection.playbook_version_id, `${connection.id} requires an attached Playbook`)
  } else {
    assert.equal(connection.playbook_id, null, `${connection.id} API access must not attach a Playbook`)
    if (connection.roles.includes("provider")) assert(connection.http, `${connection.id} API provider requires typed operations`)
  }
}

for (const run of store.runs) {
  assert(credentialIds.has(run.credential_id), `${run.id} references an unknown credential`)
  assert(!("dry_run_id" in run) && !("dry_run_playbook_id" in run), `${run.id} contains a removed dry-run binding`)
}

for (const version of store.playbookVersions) {
  assert(version.source_ids.length, `${version.id} requires source provenance`)
  for (const sourceId of version.source_ids) assert(sourceIds.has(sourceId), `${version.id} references unknown source ${sourceId}`)
  assert(version.definition.steps.every((step) => step.protected !== true), `${version.id} embeds approval policy in a Playbook`)
}

for (const source of store.playbookSources) {
  assert.equal(source.status, "ready", `${source.id} source evidence is not ready`)
  assert(source.analysis?.transcript?.length, `${source.id} has no sanitised source evidence`)
  if (source.kind === "text") assert(source.resource.startsWith("sha256:"), `${source.id} retained raw text as its resource`)
}

for (const incident of store.incidents) {
  if (incident.credential_id) assert(credentialIds.has(incident.credential_id), `${incident.id} references an unknown credential`)
  if (incident.run_id) assert(runIds.has(incident.run_id), `${incident.id} references an unknown run`)
}

for (const approval of store.approvals) {
  assert(runIds.has(approval.run_id), `${approval.id} references an unknown run`)
}

const serialized = JSON.stringify(store).toLowerCase()
for (const forbidden of ["private_key", "secret_value", "access_token", "refresh_token", "password="]) {
  assert(!serialized.includes(forbidden), `mock data contains forbidden secret field ${forbidden}`)
}

process.stdout.write(`Validated ${store.credentials.length} credentials, ${store.services.length} services, and ${store.bindings.length} bindings.\n`)
