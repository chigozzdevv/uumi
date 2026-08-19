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

assert.equal(store.overview.credentials, store.credentials.length)
assert.equal(store.overview.pending_approvals, store.approvals.filter((item) => item.decision === "pending").length)
assert.equal(store.overview.rotations_in_progress, store.runs.filter((item) => ["pending", "running", "paused", "recovering", "cleanup-required"].includes(item.status)).length)
assert.equal(store.overview.failed_rotations, store.runs.filter((item) => item.status === "failed").length)
assert.equal(store.overview.open_incidents, store.incidents.filter((item) => !["resolved", "dismissed"].includes(item.status)).length)

for (const credential of store.credentials) {
  assert(connectionIds.has(credential.connection_id), `${credential.id} references an unknown connection`)
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
}

for (const run of store.runs) {
  assert(credentialIds.has(run.credential_id), `${run.id} references an unknown credential`)
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
