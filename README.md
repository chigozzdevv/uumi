# FireKey

FireKey is an enterprise credential-rotation platform that discovers credential exposure, plans safe rotations, deploys replacement generations, verifies real application behaviour, obtains action-bound approval, revokes old credentials, and produces immutable evidence.

The product and end-to-end safety contract live in [`firekey.md`](firekey.md).

## End-to-end architecture

The product has seven deployable Python boundaries: API, incident ingestion, transactional
outbox publisher, MCP broker, stage coordinator, isolated browser worker, and IAP browser
gateway. Google Cloud Workflows owns the twelve-stage run loop; Firestore owns immutable
versions, leases, fencing tokens, approvals, sessions, and the transactional state record.

- `packages/contracts` contains canonical immutable models shared by every component.
- `packages/policy` contains deterministic stage, evidence, and approval gates.
- `packages/telemetry` contains secret-safe generation telemetry.
- `packages/testkit` contains test-only fakes that are not imported by runtime images.
- `server/core` is the provider-independent state, storage, audit, identity, inventory,
  incident, playbook, approval, and generation kernel.
- `server/connectors` contains the Google Cloud, SendGrid, GitHub, SCC, Secret Manager, and
  Cloud Run adapters.
- `server/agents` contains four separately deployed ADK agents, deterministic skill tools,
  managed sessions, approved Memory Bank context, and the per-tenant routing registry.
- `server/broker` exposes capability-scoped MCP tools. The model never receives provider or
  secret-store credentials.
- `server/coordinator` executes exact immutable playbook steps and independently verifies each
  stage before Workflows can advance it.
- `server/browser` contains the one-run VM manager, Gemini Computer Use worker, Playwright
  validation, live view, takeover gateway, and sanitised replay recorder.
- `server/capture` performs declared-field transfer directly into Secret Manager while model
  screenshots and recording are paused.
- `infra` provisions the Workflows, Eventarc, Pub/Sub, SCC, Cloud Run, IAP, one-run Compute
  Engine browser fleet, CMEK, and locked audit storage.

## Run lifecycle

```text
trigger -> preflight -> playbook -> create -> store -> deploy
        -> verify -> rollout -> observe -> approval -> revoke -> complete
```

Inventory assessment runs inside preflight; strategy selection and playbook binding run inside
the playbook stage. They are separately evidenced agent decisions without adding duplicate
workflow states.

Every mutation is bound to an organisation, run, revision, fencing token, and idempotency key.
Protected actions additionally bind the exact action digest, plan digest, evidence digest, and
credential generation. API or browser execution cannot proceed until that approval has been
granted and consumed. A completed incident run advances its linked incident from rotating, to
contained after independent revocation proof, to resolved after audit-chain verification.

Computer Use is intentionally bounded. Gemini may propose supported actions, but deterministic
code validates the immutable step, domain, unique selector, coordinates, expected and forbidden
text, current URL, policy, approval, and fencing token. Navigation is deterministic Playwright
code. Secure Capture pauses model frames and replay, transfers the declared one-time field into
Secret Manager, masks and rechecks the page, clears mutable buffers, and only then resumes. Any
ambiguity pauses for identity-bound human takeover.

## Development

Python 3.12 and `uv` are required.

```bash
make sync
make verify
make infra TERRAFORM=terraform
make images
```

`make verify` runs formatting, lint, static typing, contracts, policy, state-machine, broker,
browser, capture, dry-run, incident, connector, verifier, API, event, and publisher tests.
`make infra` formats, initialises, and validates both Terraform roots. Container builds require a
running Docker daemon.

Deployment inputs and the complete two-phase rollout are documented in
[`infra/README.md`](infra/README.md). Secret values are created outside Terraform and never
belong in source, plans, state, logs, agent context, model context, replay, or audit payloads.
