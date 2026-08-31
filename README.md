# Uumi

**A governed agent fleet for enterprise credential rotation.**

Uumi is an agentic credential rotation platform without secret exposure. It takes a credential
from an exposure event to a verified replacement, controlled rollout, revocation, and durable
proof. Agents reason over redacted facts. Deterministic services perform the mutations.

[Live app](https://uumi.web.app/) · [Technical article](https://dev.to/chigozzdev/uumi-a-governed-agent-fleet-for-enterprise-credential-rotation-3p80) · [Hackathon submission](https://allthingsagentichackathon.devpost.com/)

## Why Uumi

Credential rotation is a live migration. The provider, Secret Manager, deployed workloads,
consumer identities, traffic, and incident record must move to the same replacement generation
before the old credential can be retired. A new credential alone is not proof of a completed
rotation.

Uumi turns that migration into one durable, fenced run. Every stage is bound to an organisation,
revision, lease, fencing token, idempotency key, and target generation. The run advances only
after the required evidence is stored and independently checked.

## What Uumi does

An authenticated operator request or signed exposure event starts a run:

![Twelve-stage credential rotation flow](docs/article/diagrams/png/figure-01a.png)

The run creates the replacement, stores one exact Secret Manager version, deploys it at zero
traffic, verifies access through real consumer identities, rolls it out through planned
milestones, observes the target generation, obtains approval for protected actions, proves
provider-side revocation, retires the old version, and commits the final evidence.

## Architecture

![Uumi architecture and governed execution path](docs/article/diagrams/png/figure-01d.png)

*The coordinator, agent fleet, MCP broker, provider connectors, browser worker, and verifier share
one run contract. The [technical article](https://dev.to/chigozzdev/uumi-a-governed-agent-fleet-for-enterprise-credential-rotation-3p80)
explains the architecture and the decisions behind it.*

### Google Cloud implementation

| Responsibility | Uumi implementation |
| --- | --- |
| Agent reasoning | Four [Google Agent Development Kit (ADK)](https://adk.dev/) applications using Gemini 3.7 Flash on Vertex AI |
| Computer Use | Gemini 3.7 Flash in an isolated, one-run browser worker |
| Long-running execution | Google Cloud Workflows |
| Durable state | Firestore transactions, immutable versions, leases, fencing tokens, plans, approvals, and attempts |
| Governed tools | Capability-scoped MCP broker and a guarded browser worker |
| Service runtime | Python services on Cloud Run |
| Event delivery | Pub/Sub, Eventarc, and transactional outboxes |
| Credential storage | Google Secret Manager with immutable version references |
| Browser isolation | Shielded Compute Engine VM, no public IP, CMEK, and default-deny Secure Web Proxy egress |
| Operator access | Firebase Authentication and IAP-protected browser view and takeover |
| Evidence and telemetry | Cloud Logging, Cloud Trace, Cloud Monitoring, and redaction-safe OpenTelemetry |

The infrastructure definitions live under [`infra/terraform`](infra/terraform). The two-phase
bootstrap and deployment procedure is documented in [`infra/README.md`](infra/README.md).

## Two execution paths

Uumi supports providers with an API and providers that expose only a dashboard.

**API-managed providers.** The planner selects a typed provider operation. The MCP broker validates
the live run, lease, fence, capability, connection, and target before the connector calls the
provider. The provider's one-time value moves directly into Secret Manager.

**Dashboard-only providers.** The Playbook Builder Agent produces an immutable `PlaybookVersion`
from sanitised text or a recorded walkthrough. The Console Operator Agent proposes one supported
browser action at a time. Deterministic Playwright code validates the URL, domain, selector,
coordinates, expected text, protected-step approval, and fence before execution. Secure Capture
transfers a declared value into Secret Manager while model frames and replay recording are
paused.

Both paths converge at Store, Deploy, Verify, Rollout, Observe, Approval, Revoke, and Complete.

## Agent fleet and authority

| Agent | Decision it returns |
| --- | --- |
| Inventory Assessment | Consumer coverage and inventory findings |
| Planner | Rotation strategy, rollout milestones, and recovery choice |
| Playbook Builder | Versioned browser steps, checkpoints, and declared fields |
| Console Operator | The next supported browser action for the current immutable step |

Agents receive run-bound, redacted context and return typed decisions. The coordinator applies
stage gates. The MCP broker and browser worker enforce tool authority. The verifier independently
checks provider state, Secret Manager state, runtime readiness, consumer access, and generation-
scoped telemetry. A protected action cannot run until its exact action digest has an approval and a
one-time capability.

## Security boundary

- API-created credential values travel from the provider connector to Secret Manager and are
  cleared before control returns to the caller.
- Browser-disclosed values use Secure Capture. The model and replay recorder are paused during
  transfer; only the declared field is written.
- Agents see metadata, fingerprints, resource identifiers, and sanitised walkthrough observations.
  Plaintext credentials never enter model context, prompts, tool responses, logs, traces, evidence,
  or audit payloads.
- Revisions, leases, and fencing tokens reject stale workers and stale resume requests. Reconcile
  runs before a retry; cleanup removes only an orphan attributable to the expired attempt.
- Revocation is proved before the old Secret Manager version is disabled. Compensation is recorded
  as its own terminal outcome.

## Repository layout

| Path | Contents |
| --- | --- |
| `server/api` | Authenticated control API and route surface |
| `server/agents` | ADK agents, managed runtime integration, and agent deployment |
| `server/coordinator` | Deterministic stage execution and state gates |
| `server/verifier` | Independent probes and generation-scoped verification |
| `server/broker` | Capability-scoped MCP server and tool validation |
| `server/browser` and `server/capture` | Isolated Computer Use worker, IAP gateway, and Secure Capture |
| `server/ingestion`, `server/publisher`, `server/notification`, `server/auditlog` | Signed intake, durable event delivery, notifications, and audit delivery |
| `server/connectors` | Provider, Cloud Run, Secret Manager, GitHub, Google Cloud, and video adapters |
| `packages/contracts` | Immutable domain contracts shared across services |
| `packages/policy` | Deterministic policy and evidence gates |
| `packages/telemetry` | Secret-safe tracing and metrics instrumentation |
| `client` | React, TypeScript, Tailwind CSS, and Vite operator dashboard |
| `infra/terraform` | Google Cloud foundation, runtimes, eventing, identity, and perimeter |

## Run locally

### Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Node.js and npm
- Docker, for runtime images or emulator-backed integration tests
- Terraform and the Google Cloud CLI, for Google Cloud deployment

### Install dependencies

From the repository root:

```sh
make sync
```

This runs `uv sync --all-packages --all-extras --locked` and `npm --prefix client ci`.

### Start the API

The API is cloud-backed. It needs Application Default Credentials and real project resources for
Firestore, Secret Manager, the evidence bucket, and the configured browser gateway.

```sh
cp .env.example .env
```

Fill `.env` with the project-specific values. Keep secret material as immutable Secret Manager
version references; never paste a credential value into `.env`. Then authenticate locally and start
the service:

```sh
gcloud auth application-default login
set -a
source .env
set +a
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Check the process with:

```sh
curl http://127.0.0.1:8000/health/live
```

The API imports from the workspace after `make sync`; no generated source is required.

### Start the dashboard

In a second terminal:

```sh
cp client/.env.example client/.env.local
```

Set the public Firebase web-app values in `client/.env.local`. Leave `VITE_API_URL` empty when
using the local API. `UUMI_DEV_API_URL` defaults to `http://127.0.0.1:8000` and controls Vite's
`/v1` and `/health` proxy targets.

```sh
npm --prefix client run dev
```

Open `http://127.0.0.1:5173`. The dashboard uses Firebase Authentication and sends the signed-in
user's ID token with each API request.

### Run integration tests with emulators

The normal test suite uses fakes. The durable Firestore and Pub/Sub tests require the emulators:

```sh
docker run --detach --name uumi-firestore --publish 8787:8787 \
  gcr.io/google.com/cloudsdktool/google-cloud-cli:528.0.0-emulators \
  gcloud emulators firestore start --host-port=0.0.0.0:8787

docker run --detach --name uumi-pubsub --publish 8788:8788 \
  gcr.io/google.com/cloudsdktool/google-cloud-cli:528.0.0-emulators \
  gcloud beta emulators pubsub start --host-port=0.0.0.0:8788 --project=uumi-test
```

After both containers are ready:

```sh
FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 \
PUBSUB_EMULATOR_HOST=127.0.0.1:8788 \
uv run pytest server/tests/integration -m integration
```

## Verification and builds

```sh
make verify
```

`make verify` runs Ruff formatting and linting, mypy, Python tests, and the client lint/type/build
check. Build all runtime images with a running Docker daemon:

```sh
make images
```

Validate both Terraform roots without applying changes:

```sh
make infra TERRAFORM=terraform
```

Render the article diagrams from their Mermaid sources:

```sh
node docs/article/diagrams/render.mjs
```

## Deploy to Google Cloud

Deployment is intentionally explicit and two-phase:

1. Bootstrap versioned Terraform state.
2. Apply the foundation with organisation, policy, identity, perimeter, and secret-container
   inputs.
3. Build and push every runtime image by digest.
4. Apply the runtime graph and register the four ADK agents.
5. Run the operational readiness checks before enabling schedules or webhooks.

Follow [`infra/README.md`](infra/README.md) for the required variables, IAM grants, secret-version
creation, image publishing, agent deployment, and readiness checks. Terraform never receives
credential values. It receives resource names and immutable version references only.

## License and security

This repository is a hackathon implementation. Do not use production credentials in local tests or
demo data. Report a security issue privately to [uumi@muwa.io](mailto:uumi@muwa.io) rather than
opening a public issue with sensitive details.
