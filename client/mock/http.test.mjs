import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { createStore } from "./data.mjs"

const port = 20000 + Math.floor(Math.random() * 10000)
const root = `http://127.0.0.1:${port}/v1/organisations/org_acme`
const server = spawn(process.execPath, [new URL("./server.mjs", import.meta.url).pathname], {
  env: { ...process.env, FIREKEY_MOCK_PORT: String(port) },
  stdio: "ignore",
})

async function request(path, options) {
  const response = await fetch(`${root}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  })
  return { response, body: await response.json() }
}

try {
  let ready = false
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`)
      if (response.ok) { ready = true; break }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50))
    }
  }
  assert(ready, "mock server did not start")

  const providerCredentials = await request("/inventory/connections/conn_sendgrid/credential-metadata")
  assert.equal(providerCredentials.response.status, 200)
  assert.equal(providerCredentials.body[0].provider_id, "sg_key_7710")
  assert.deepEqual(providerCredentials.body[0].scopes, ["mail.send"])
  assert.equal(Object.hasOwn(providerCredentials.body[0], "secret"), false)

  const verifiedCredential = await request("/inventory/connections/conn_sendgrid/verify-credential", {
    method: "POST",
    body: JSON.stringify({ secret_store_connection_id: "conn_secrets", provider_id: "sg_key_7710", secret_reference: "projects/acme-prod/secrets/customer-notifications/versions/1" }),
  })
  assert.equal(verifiedCredential.response.status, 200)
  assert.equal(verifiedCredential.body.verified, true)

  const runtimeResources = await request("/inventory/connections/conn_runtime/runtime-resources")
  assert.equal(runtimeResources.response.status, 200)
  assert(runtimeResources.body.some((entry) => entry.display_name === "inventory-reporter"))
  assert.equal(Object.hasOwn(runtimeResources.body[0], "secret"), false)

  const detail = await request("/inventory/credentials/cred_sendgrid")
  assert.equal(detail.response.status, 200)
  assert.equal(detail.body.display_name, "production-password-emailer")

  const changed = await request("/inventory/credentials/cred_sendgrid", {
    method: "PATCH",
    body: JSON.stringify({ expected_revision: 8, display_name: "production-emailer" }),
  })
  assert.equal(changed.response.status, 200)
  assert.equal(changed.body.revision, 9)

  const stale = await request("/inventory/credentials/cred_sendgrid", {
    method: "PATCH",
    body: JSON.stringify({ expected_revision: 8, display_name: "stale-name" }),
  })
  assert.equal(stale.response.status, 409)
  assert.equal(stale.body.code, "conflict")

  const existingControls = await request("/inventory/credentials/cred_sendgrid/controls/control_sendgrid_v1")
  assert.equal(existingControls.response.status, 200)
  assert.equal(existingControls.body.credential_id, "cred_sendgrid")

  const controlsUpdate = await request("/inventory/credentials/cred_sendgrid/controls", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 9, version_id: "control_sendgrid_v2", controls: { automatic_triggers: ["expiry", "drift"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800 } }),
  })
  assert.equal(controlsUpdate.response.status, 201)
  assert.equal(controlsUpdate.body.credential.control_version, "control_sendgrid_v2")
  assert.equal(controlsUpdate.body.credential.revision, 10)
  assert.equal(controlsUpdate.body.controls.number, 2)

  const previousControls = await request("/inventory/credentials/cred_sendgrid/controls/control_sendgrid_v1")
  assert.equal(previousControls.response.status, 200)

  const staleControls = await request("/inventory/credentials/cred_sendgrid/controls", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 9, version_id: "control_sendgrid_v3", controls: { automatic_triggers: ["expiry"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800 } }),
  })
  assert.equal(staleControls.response.status, 409)

  const fixture = createStore()
  const service = fixture.services.find((entry) => entry.id === "svc_notifications")
  const createdAt = new Date().toISOString()
  const secretReference = "projects/acme-prod/secrets/customer-notifications/versions/1"
  const imported = await request("/inventory/credentials", {
    method: "POST",
    body: JSON.stringify({
      credential: { id: "cred_new_mailer", organisation_id: "org_acme", connection_id: "conn_sendgrid", secret_store_connection_id: "conn_secrets", secret_resource: "projects/acme-prod/secrets/customer-notifications", secret_reference: secretReference, provider: "sendgrid", kind: "api-key", display_name: "new-mailer", provider_id: "sg_key_7710", scopes: ["mail.send"], consumer_ids: [service.id], active_generation_id: "gen_new_mailer", control_version: "control_new_mailer_v1", created_at: createdAt, updated_at: createdAt, revision: 0 },
      generation: { id: "gen_new_mailer", organisation_id: "org_acme", credential_id: "cred_new_mailer", provider_id: "sg_key_7710", fingerprint: null, scopes: ["mail.send"], state: "active", attempt_id: "attempt_new_mailer", secret_reference: secretReference, predecessor_id: null, successor_id: null, created_at: createdAt, revoked_at: null },
      bindings: [{ id: "binding_new_mailer", organisation_id: "org_acme", credential_id: "cred_new_mailer", service_id: service.id, environment_id: service.environment_id, runtime_connection_id: service.runtime_connection_id, runtime_resource: service.runtime_resource, runtime_secret_name: "NEW_MAILER_KEY", secret_reference: secretReference, current_generation_id: "gen_new_mailer", target_generation_id: null, verification_id: "verify_new_mailer", required: true, revision: 0 }],
      controls: { automatic_triggers: ["expiry", "drift"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800 },
    }),
  })
  assert.equal(imported.response.status, 201)
  assert.equal(imported.body.control_version, "control_new_mailer_v1")
  const importedControls = await request("/inventory/credentials/cred_new_mailer/controls/control_new_mailer_v1")
  assert.equal(importedControls.response.status, 200)
  assert.equal(importedControls.body.number, 1)

  const started = await request("/runs", {
    method: "POST",
    body: JSON.stringify({ credential_id: "cred_new_mailer", control_version: "control_new_mailer_v1", event_id: "manual-mock-test", reason: "Verify the mock lifecycle", urgency: "routine", received_at: createdAt }),
  })
  assert.equal(started.response.status, 201)
  assert.equal(started.body.run.status, "pending")
  await new Promise((resolve) => setTimeout(resolve, 1450))
  const awaitingApproval = await request(`/runs/${started.body.run.id}`)
  assert.equal(awaitingApproval.body.stage, "approval")
  assert.equal(awaitingApproval.body.status, "paused")
  const approvals = await request("/approvals")
  const approval = approvals.body.find((entry) => entry.run_id === started.body.run.id && entry.decision === "pending")
  assert(approval)
  const approved = await request(`/approvals/${approval.id}/decision`, {
    method: "POST",
    body: JSON.stringify({ expected_revision: approval.revision, decision: "approved" }),
  })
  assert.equal(approved.response.status, 200)
  assert.equal(approved.body.consumed_at, approved.body.decided_at)
  await new Promise((resolve) => setTimeout(resolve, 350))
  const completed = await request(`/runs/${started.body.run.id}`)
  assert.equal(completed.body.stage, "complete")
  assert.equal(completed.body.status, "completed")

  const source = await request("/playbooks/playbook_http_test/walkthroughs/references", {
    method: "POST",
    body: JSON.stringify({ source_id: "source_http_test", kind: "text", content: "Create and capture the replacement, then revoke the prior credential." }),
  })
  assert.equal(source.response.status, 201)
  const definition = { ...fixture.playbookVersions[0].definition, name: "HTTP lifecycle test", platform: "internal-vendor" }
  const draft = await request("/playbooks/playbook_http_test/build", {
    method: "POST",
    headers: { "Idempotency-Key": "build-http-test" },
    body: JSON.stringify({ version_id: "playbook_http_test_v1", objective: JSON.stringify(definition), source_ids: [source.body.id] }),
  })
  assert.equal(draft.response.status, 201)
  assert.equal(draft.body.version.state, "draft")
  assert.equal(draft.body.playbook.active_version_id, null)
  const draftDetail = await request("/playbooks/playbook_http_test")
  assert.equal(draftDetail.body.active_version, null)
  assert.equal(draftDetail.body.latest_version.id, "playbook_http_test_v1")
  const published = await request("/playbooks/playbook_http_test/versions/playbook_http_test_v1/publish", { method: "POST", body: "{}" })
  assert.equal(published.response.status, 200)
  assert.equal(published.body.state, "published")
  const publishedDetail = await request("/playbooks/playbook_http_test")
  assert.equal(publishedDetail.body.active_version.id, "playbook_http_test_v1")

  const blockedArchive = await request("/inventory/credentials/cred_sendgrid/archive", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 10 }),
  })
  assert.equal(blockedArchive.response.status, 409)

  const cascadedArchive = await request("/inventory/credentials/cred_sendgrid/archive", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 10, cascade: true }),
  })
  assert.equal(cascadedArchive.response.status, 200)
  const archivedGraph = await request("/inventory/graph")
  assert.equal(archivedGraph.body.credentials.some((entry) => entry.id === "cred_sendgrid"), false)
  assert.equal(archivedGraph.body.bindings.some((entry) => entry.credential_id === "cred_sendgrid"), false)

  const graph = await request("/inventory/graph")
  assert.equal(graph.response.status, 200)
  assert.equal(graph.body.credentials.some((entry) => entry.id === "cred_sendgrid"), false)

  process.stdout.write("Validated provider metadata, detail, immutable controls, lifecycle progression, approvals, draft review, archive, and refresh behavior.\n")
} finally {
  server.kill("SIGTERM")
}
