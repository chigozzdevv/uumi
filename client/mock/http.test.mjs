import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { createStore } from "./data.mjs"

const port = 20000 + Math.floor(Math.random() * 10000)
const root = `http://127.0.0.1:${port}/v1/organisations/org_acme`
const server = spawn(process.execPath, [new URL("./server.mjs", import.meta.url).pathname], {
  env: { ...process.env, FIREKEY_MOCK_PORT: String(port), FIREKEY_MOCK_ROTATION_STEP_MS: "150" },
  stdio: ["ignore", "ignore", "inherit"],
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

  const rotationHistory = await request("/runs/run_vendor_failed/history")
  assert.equal(rotationHistory.response.status, 200)
  assert.equal(rotationHistory.body.stages.some((entry) => entry.agent_decisions.some((decision) => decision.agent === "operator")), false)
  assert.equal(rotationHistory.body.stages.some((entry) => entry.browser_actions.length > 0), false)
  assert.deepEqual(rotationHistory.body.computer_use, [])
  assert.equal("replay" in rotationHistory.body, false)

  const computerHistory = await request("/runs/run_vendor_complete/history")
  const modelInputs = computerHistory.body.computer_use.filter((entry) => entry.phase === "input")
  const modelInput = modelInputs[0]
  assert(modelInput)
  assert.deepEqual(modelInputs.map((entry) => entry.stage), ["create", "create", "revoke", "revoke"])
  assert.deepEqual(modelInputs.map((entry) => entry.prompt), [
    "Open the credential creation form",
    "Submit the credential creation form",
    "Select the previous credential",
    "Revoke the previous credential",
  ])
  assert.deepEqual(modelInputs.map((entry) => entry.effect), [
    "none",
    "create-credential",
    "none",
    "revoke-credential",
  ])
  assert(modelInputs.every((entry) => !entry.prompt.includes("approved playbook")))
  assert(computerHistory.body.computer_use.some((entry) => entry.phase === "thought"))
  assert(computerHistory.body.computer_use.some((entry) => entry.phase === "validation"))
  const modelImage = await fetch(`${root}/runs/run_vendor_complete/computer-use/${modelInput.id}/image`)
  assert.equal(modelImage.status, 200)
  assert.equal(modelImage.headers.get("cache-control"), "private, no-store")
  assert.match(modelImage.headers.get("content-type"), /^image\//)
  assert.equal((await modelImage.text()).includes("sg_key_"), false)
  const revokeInput = modelInputs.find((entry) => entry.stage === "revoke")
  assert(revokeInput)
  const revokeImage = await fetch(`${root}/runs/run_vendor_complete/computer-use/${revokeInput.id}/image`)
  assert.equal(revokeImage.status, 200)
  assert.equal((await revokeImage.text()).includes("Revoke credential"), true)
  assert.equal(computerHistory.body.stages.find((entry) => entry.stage === "approval").summary, "Revocation approved")
  assert.deepEqual(computerHistory.body.stages.find((entry) => entry.stage === "complete").details, [])

  const triggerHistory = await request("/runs/run_emergency_sendgrid/history")
  const triggerStage = triggerHistory.body.stages.find((entry) => entry.stage === "trigger")
  assert.equal(triggerStage.summary, "Exposure alert started rotation")
  assert.deepEqual(triggerStage.details, [
    { label: "Configured trigger", value: "GitHub Secret Scanning" },
    { label: "Reason", value: "Verified SendGrid key exposure in a public repository" },
  ])

  const approvalEvidence = await request("/approvals/approval_sendgrid_revoke/evidence")
  assert.equal(approvalEvidence.response.status, 200)
  assert.equal(approvalEvidence.body.status, "passed")
  assert.deepEqual(approvalEvidence.body.checks, ["provider-valid", "store-valid", "deployment-valid", "old-use-clear", "rollback-ready"])

  const scheduledHistory = await request("/runs/run_github_schedule/history")
  const scheduledTrigger = scheduledHistory.body.stages.find((entry) => entry.stage === "trigger")
  assert.equal(scheduledTrigger.summary, "Rotation started on schedule")
  assert.deepEqual(scheduledTrigger.details, [
    { label: "Configured trigger", value: "Scheduled rotation" },
    { label: "Reason", value: "The configured rotation time was reached." },
  ])

  const githubOnboarding = await request("/github/onboarding", { method: "POST", body: "{}" })
  assert.equal(githubOnboarding.response.status, 201)
  assert.equal(githubOnboarding.body.session.status, "pending")
  const githubInstall = new URL(githubOnboarding.body.installation_url)
  assert.equal(githubInstall.searchParams.get("installation_id"), "123")
  const githubCallback = new URL(githubOnboarding.body.authorization_url)
  const githubDiscovery = await request(`/github/onboarding/${githubOnboarding.body.session.id}/discover`, {
    method: "POST",
    body: JSON.stringify({ state: githubCallback.searchParams.get("state"), pkce_verifier: githubOnboarding.body.pkce_verifier, code: githubCallback.searchParams.get("code"), installation_id: 123 }),
  })
  assert.equal(githubDiscovery.response.status, 200)
  assert.equal(githubDiscovery.body.session.status, "discovered")
  assert.equal(githubDiscovery.body.repositories[0].full_name, "acme/store-workers")
  const githubCompletion = await request(`/github/onboarding/${githubOnboarding.body.session.id}/complete`, {
    method: "POST",
    body: "{}",
  })
  assert.equal(githubCompletion.response.status, 200)
  assert.equal(githubCompletion.body.session.status, "complete")
  assert.equal(githubCompletion.body.repositories[0].full_name, "acme/store-workers")

  const googleOnboarding = await request("/google-cloud/onboarding", { method: "POST", body: "{}" })
  assert.equal(googleOnboarding.response.status, 201)
  assert.equal(googleOnboarding.body.session.status, "pending")
  const callback = new URL(googleOnboarding.body.authorization_url)
  const googleDiscovery = await request(`/google-cloud/onboarding/${googleOnboarding.body.session.id}`, {
    method: "POST",
    body: JSON.stringify({ state: callback.searchParams.get("state"), pkce_verifier: googleOnboarding.body.pkce_verifier, code: callback.searchParams.get("code") }),
  })
  assert.equal(googleDiscovery.response.status, 200)
  assert.equal(googleDiscovery.body.projects[0].project_id, "acme-prod")
  assert.equal(googleDiscovery.body.projects[0].services[0].region, "us-central1")
  const googleConnection = await request(`/google-cloud/onboarding/${googleOnboarding.body.session.id}/connection`, {
    method: "POST",
    body: JSON.stringify({ project_id: "acme-prod", automation_identity: "firekey-automation@acme-prod.iam.gserviceaccount.com" }),
  })
  assert.equal(googleConnection.response.status, 201)
  assert.equal(googleConnection.body.connection.platform, "google-cloud")
  assert.deepEqual(googleConnection.body.connection.roles, ["runtime", "secret-store"])
  assert.match(googleConnection.body.grant_command, /firekey-broker@/)
  const verifiedGoogleConnection = await request(`/google-cloud/onboarding/${googleOnboarding.body.session.id}/connection/verify`, {
    method: "POST",
    body: JSON.stringify({ expected_revision: googleConnection.body.connection.revision }),
  })
  assert.equal(verifiedGoogleConnection.response.status, 200)
  assert.equal(verifiedGoogleConnection.body.status, "ready")

  const setupFixture = createStore()
  for (const [sourceId, newId] of [
    ["conn_sendgrid", "conn_http_test"],
    ["conn_runtime", "conn_runtime_test"],
    ["conn_secrets", "conn_secrets_test"],
  ]) {
    const source = setupFixture.connections.find((entry) => entry.id === sourceId)
    const candidate = { ...structuredClone(source), id: newId, display_name: `${source.display_name} test`, status: "setup-required", authenticated_at: null, last_validated_at: null, revision: 0 }
    const connected = await request("/inventory/connections", { method: "POST", body: JSON.stringify(candidate) })
    assert.equal(connected.response.status, 201)
    assert.equal(connected.body.status, "ready")
    assert(connected.body.last_validated_at)
  }

  const providerCredentials = await request("/inventory/connections/conn_sendgrid/credential-metadata")
  assert.equal(providerCredentials.response.status, 200)
  const providerCredential = providerCredentials.body.find((entry) => entry.provider_id === "sg_key_7710")
  assert(providerCredential)
  assert.deepEqual(providerCredential.scopes, ["mail.send"])
  assert.equal(Object.hasOwn(providerCredential, "secret"), false)

  const resolvedCredential = await request("/inventory/connections/conn_sendgrid/resolve-credential", {
    method: "POST",
    body: JSON.stringify({ secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/customer-notifications/versions/1" }),
  })
  assert.equal(resolvedCredential.response.status, 200)
  assert.equal(resolvedCredential.body.provider_id, "sg_key_7710")
  assert.equal(resolvedCredential.body.kind, "api-key")

  const importedCredential = await request("/inventory/connections/conn_sendgrid/resolve-credential", {
    method: "POST",
    body: JSON.stringify({ secret_store_connection_id: "conn_secrets", secret_reference: "projects/acme-prod/secrets/sendgrid/versions/7" }),
  })
  assert.equal(importedCredential.response.status, 200)
  assert.equal(importedCredential.body.provider_id, "sg_key_4902")

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
    body: JSON.stringify({ expected_revision: 9, version_id: "control_sendgrid_v2", controls: { automatic_triggers: ["expiry", "drift"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800, require_revoke_approval: false, exposure_sources: [] } }),
  })
  assert.equal(controlsUpdate.response.status, 201)
  assert.equal(controlsUpdate.body.credential.control_version, "control_sendgrid_v2")
  assert.equal(controlsUpdate.body.credential.revision, 10)
  assert.equal(controlsUpdate.body.controls.number, 2)

  const previousControls = await request("/inventory/credentials/cred_sendgrid/controls/control_sendgrid_v1")
  assert.equal(previousControls.response.status, 200)

  const staleControls = await request("/inventory/credentials/cred_sendgrid/controls", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 9, version_id: "control_sendgrid_v3", controls: { automatic_triggers: ["expiry"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800, require_revoke_approval: false, exposure_sources: [] } }),
  })
  assert.equal(staleControls.response.status, 409)

  const fixture = createStore()
  const runtimeResource = fixture.runtimeResources.find((entry) => entry.display_name === "customer-sync")
  const createdAt = new Date().toISOString()
  const secretReference = "projects/acme-prod/secrets/customer-notifications/versions/1"
  const imported = await request("/inventory/credentials", {
    method: "POST",
    body: JSON.stringify({
      credential: { id: "cred_new_mailer", organisation_id: "org_acme", connection_id: "conn_sendgrid", secret_store_connection_id: "conn_secrets", secret_resource: "projects/acme-prod/secrets/customer-notifications", secret_reference: secretReference, provider: "sendgrid", kind: "api-key", display_name: "new-mailer", provider_id: "sg_key_7710", scopes: ["mail.send"], consumer_ids: ["svc_new_mailer"], active_generation_id: "gen_new_mailer", control_version: "control_new_mailer_v1", created_at: createdAt, updated_at: createdAt, revision: 0 },
      generation: { id: "gen_new_mailer", organisation_id: "org_acme", credential_id: "cred_new_mailer", provider_id: "sg_key_7710", fingerprint: null, scopes: ["mail.send"], state: "active", attempt_id: "attempt_new_mailer", secret_reference: secretReference, predecessor_id: null, successor_id: null, created_at: createdAt, revoked_at: null },
      consumer: { application_id: "app_new_mailer", environment_id: "env_new_mailer", service_id: "svc_new_mailer", binding_id: "binding_new_mailer", runtime_connection_id: runtimeResource.connection_id, runtime_resource: runtimeResource.reference, runtime_secret_name: "CUSTOMER_NOTIFICATIONS" },
      controls: { automatic_triggers: ["expiry", "drift"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800, require_revoke_approval: true, exposure_sources: [] },
    }),
  })
  assert.equal(imported.response.status, 201)
  assert.equal(imported.body.control_version, "control_new_mailer_v1")
  const importedGraph = await request("/inventory/graph")
  assert(importedGraph.body.services.some((entry) => entry.id === "svc_new_mailer" && entry.display_name === "customer-sync"))
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
  assert.equal(approved.body.consumed_at, null)
  await new Promise((resolve) => setTimeout(resolve, 350))
  const completed = await request(`/runs/${started.body.run.id}`)
  assert.equal(completed.body.stage, "complete")
  assert.equal(completed.body.status, "completed")

  const automaticControls = await request("/inventory/credentials/cred_new_mailer/controls", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 0, version_id: "control_new_mailer_v2", controls: { automatic_triggers: ["expiry", "drift"], rotate_before_expiry_seconds: 604800, maximum_observation_seconds: 1800, require_revoke_approval: false, exposure_sources: [] } }),
  })
  assert.equal(automaticControls.response.status, 201)
  assert.equal(automaticControls.body.controls.definition.require_revoke_approval, false)
  assert.deepEqual(automaticControls.body.controls.definition.protected_tools, [])

  const automatic = await request("/runs", {
    method: "POST",
    body: JSON.stringify({ credential_id: "cred_new_mailer", control_version: "control_new_mailer_v2", event_id: "manual-automatic-test", reason: "Verify the automatic mock lifecycle", urgency: "routine", received_at: createdAt }),
  })
  assert.equal(automatic.response.status, 201)
  await new Promise((resolve) => setTimeout(resolve, 1800))
  const automaticCompleted = await request(`/runs/${automatic.body.run.id}`)
  assert.equal(automaticCompleted.body.stage, "complete")
  assert.equal(automaticCompleted.body.status, "completed")
  const automaticApprovals = await request("/approvals")
  assert.equal(automaticApprovals.body.some((entry) => entry.run_id === automatic.body.run.id), false)

  const source = await request("/playbooks/playbook_http_test/walkthroughs/references", {
    method: "POST",
    body: JSON.stringify({ source_id: "source_http_test", kind: "text", content: "Create and capture the replacement, then revoke the prior credential." }),
  })
  assert.equal(source.response.status, 201)
  const video = await request("/playbooks/playbook_video_test/walkthroughs", {
    method: "POST",
    body: JSON.stringify({ source_id: "source_video_test", content_type: "video/mp4", size: 5, crc32c: "mnG7TA==" }),
  })
  assert.equal(video.response.status, 201)
  const uploaded = await fetch(video.body.upload_url, {
    method: "PUT",
    headers: { "Content-Type": "video/mp4", "Content-Range": "bytes 0-4/5" },
    body: Buffer.from("video"),
  })
  assert.equal(uploaded.status, 200)
  const analysed = await request("/playbooks/playbook_video_test/walkthroughs/source_video_test/complete", { method: "POST", body: "{}" })
  assert.equal(analysed.body.status, "ready")
  assert.equal(analysed.body.analysis.processor, "google-video-intelligence")
  const preview = await request("/playbooks/playbook_video_test/draft", {
    method: "POST",
    headers: { "Idempotency-Key": "draft-video-test" },
    body: JSON.stringify({ objective: "Build a browser credential-rotation procedure for the exact platform \"internal-vendor\".", source_ids: ["source_video_test"] }),
  })
  assert.equal(preview.response.status, 200)
  assert.equal(preview.body.definition.platform, "internal-vendor")
  assert(preview.body.definition.steps.every((step) => step.objective && step.checkpoint))
  const secureCreate = preview.body.definition.steps.find((step) => step.effect === "create-credential")
  const protectedRevoke = preview.body.definition.steps.find((step) => step.effect === "revoke-credential")
  assert.notEqual(secureCreate.selectors[0].value, secureCreate.secure_field.selector.value)
  assert.equal(protectedRevoke.tool, "browser.revokeCredential")
  const unpersisted = await request("/playbooks/playbook_video_test")
  assert.equal(unpersisted.response.status, 404)
  const savedVideoPlaybook = await request("/playbooks/playbook_video_test/versions", {
    method: "POST",
    body: JSON.stringify({ version_id: "playbook_video_test_v1", definition: preview.body.definition, source_ids: ["source_video_test"] }),
  })
  assert.equal(savedVideoPlaybook.response.status, 201)
  assert.equal(savedVideoPlaybook.body.playbook.latest_version, 1)
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

  const activeArchive = await request("/inventory/credentials/cred_sendgrid/archive", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 10, cascade: true }),
  })
  assert.equal(activeArchive.response.status, 200)
  const cancelledRun = await request("/runs/run_emergency_sendgrid")
  assert.equal(cancelledRun.body.status, "cancelled")
  const dismissedIncident = await request("/incidents/incident_github_1842")
  assert.equal(dismissedIncident.body.status, "dismissed")
  const cancelledApprovals = await request("/approvals")
  assert.equal(cancelledApprovals.body.find((entry) => entry.id === "approval_sendgrid_revoke").decision, "cancelled")
  const archivedGraph = await request("/inventory/graph")
  assert.equal(archivedGraph.body.credentials.some((entry) => entry.id === "cred_sendgrid"), false)
  assert.equal(archivedGraph.body.bindings.some((entry) => entry.credential_id === "cred_sendgrid"), false)

  const graph = await request("/inventory/graph")
  assert.equal(graph.response.status, 200)
  assert.equal(graph.body.credentials.some((entry) => entry.id === "cred_sendgrid"), false)

  const incident = await request("/incidents/incident_scc_9921")
  assert.equal(incident.body.credential_id, null)
  const confirmedIncident = await request("/incidents/incident_scc_9921/confirm", {
    method: "POST",
    body: JSON.stringify({ expected_revision: incident.body.revision, credential_id: "cred_vendor" }),
  })
  assert.equal(confirmedIncident.response.status, 200)
  assert.equal(confirmedIncident.body.credential_id, "cred_vendor")
  const incidentRotation = await request("/incidents/incident_scc_9921/rotate", {
    method: "POST",
    body: JSON.stringify({ control_version: "control_vendor_v1", reason: "Respond to verified exposure", urgency: "urgent", received_at: new Date().toISOString() }),
  })
  assert.equal(incidentRotation.response.status, 201)
  assert.equal(incidentRotation.body.incident.status, "rotation-started")
  assert.equal(incidentRotation.body.run.trigger.source, "security-command-center")

  const destinations = await request("/notifications/endpoints")
  assert.equal(destinations.response.status, 200)
  assert.equal(destinations.body[0].email_address, "security@acme.example")
  assert.equal(Object.hasOwn(destinations.body[0], "auth_reference"), false)
  const notificationTopics = await request("/notifications/topics")
  assert.equal(notificationTopics.response.status, 200)
  assert.ok(notificationTopics.body.some((entry) => entry.id === "approvals"))
  const createdDestination = await request("/notifications/endpoints", {
    method: "POST",
    body: JSON.stringify({
      id: "endpoint_http_email",
      email_address: "security@acme.example",
      topics: ["rotation-failures", "approvals"],
    }),
  })
  assert.equal(createdDestination.response.status, 201)
  assert.equal(createdDestination.body.enabled, true)
  const pausedDestination = await request("/notifications/endpoints/endpoint_http_email/state", {
    method: "POST",
    body: JSON.stringify({ expected_revision: 0, enabled: false }),
  })
  assert.equal(pausedDestination.response.status, 200)
  assert.equal(pausedDestination.body.enabled, false)

  const profile = await request("/settings/profile")
  assert.equal(profile.response.status, 200)
  assert.equal(profile.body.connected_via, "Google")
  const updatedProfile = await request("/settings/profile", {
    method: "PATCH",
    body: JSON.stringify({ expected_revision: profile.body.revision, display_name: "Chigozie O." }),
  })
  assert.equal(updatedProfile.response.status, 200)
  assert.equal(updatedProfile.body.display_name, "Chigozie O.")
  const invitedMember = await request("/settings/team/invitations", {
    method: "POST",
    body: JSON.stringify({ email: "new.member@acme.example", role: "viewer" }),
  })
  assert.equal(invitedMember.response.status, 201)
  assert.equal(invitedMember.body.status, "pending")
  const team = await request("/settings/team")
  assert(team.body.some((member) => member.email === "new.member@acme.example"))
  const logout = await fetch(`http://127.0.0.1:${port}/v1/auth/logout`, { method: "POST" })
  assert.equal(logout.status, 204)
  assert.equal(logout.headers.get("clear-site-data"), '"cache", "cookies", "storage"')

  process.stdout.write("Validated Computer Use model history, provider metadata, detail, immutable controls, lifecycle progression, approvals, settings, notifications, draft review, archive, and refresh behavior.\n")
} finally {
  server.kill("SIGTERM")
}
