# Uumi infrastructure

Uumi uses a two-phase infrastructure deployment. The foundation is created first so images,
Secret Manager versions, organisation grants, ingestion sources, and IAP users can be supplied as real
immutable inputs. The second apply creates all nine runtimes, the Workflows coordinator, event
routing, isolated browser fleet, and browser gateway together. Terraform rejects a partial
runtime deployment.

## 1. Bootstrap remote state

```bash
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap apply \
  -var=project_id=YOUR_PROJECT \
  -var=bucket_name=YOUR_STATE_BUCKET
```

The state bucket has versioning, uniform access, public-access prevention, and destruction
protection.

## 2. Apply the foundation

Copy `infra/terraform/environments/dev/values.example.tfvars` to an untracked `values.tfvars`.
Keep all nine image variables, `notification_app_url`, and `capability_secret_version` null for
the first apply. Supply the organisation Access Context Manager policy, an existing operator
access level constrained by your organisation's trusted identities, devices, or networks, and the
browser provider-domain allowlist from the beginning; these are infrastructure controls, not
credential values.

```bash
terraform -chdir=infra/terraform/environments/dev init \
  -backend-config=bucket=YOUR_STATE_BUCKET \
  -backend-config=prefix=uumi/dev
terraform -chdir=infra/terraform/environments/dev apply \
  -var-file=values.tfvars
```

This creates the protected Firestore database, service accounts, immutable Artifact Registry,
CMEK keys, locked evidence and audit storage, Agent Runtime staging bucket, GitHub App OAuth and
webhook secret containers, provider webhook secret containers, capability secret container,
service perimeter, regional policy, private browser network, one-run VM template, and Identity
Platform sign-in configuration (email and password enabled; `identity_platform_domains` admits the
client origin). Computer Use remains unavailable until its on-demand secure-egress lifecycle is
implemented.

Create secret versions outside Terraform. The capability secret version must contain exactly the
raw 32-byte private key of an Ed25519 keypair; `capability_public_key` contains only the paired raw
public key in unpadded base64url form. Only the API and coordinator can read the private key.
Broker, gateway, and one-run browser workers receive the public key and therefore cannot mint
capabilities. The GitHub App uses one random webhook HMAC secret and one OAuth client secret;
customer installations are mapped to Uumi organisations in Firestore only after a signed
installation delivery and a PKCE-bound user authorization prove ownership. Provider webhooks use
a distinct random HMAC secret per configured source. Provider signatures cover
`X-Uumi-Timestamp + "." + raw-body` and Uumi
rejects timestamps outside the configured replay window. Do not
place private or HMAC values in Terraform variables, plans, state, commands, or shell history.

Register a Google OAuth web client with the Uumi callback URL ending in
`?google_cloud=callback`. Add its client secret as an immutable Secret Manager version, then set
the three `google_cloud_*` variables together. The short-lived user token is used only to discover
visible projects, Cloud Run services, and service accounts during onboarding; it is cleared before
the response and is never stored in Firestore or returned to the dashboard.

For each Google Cloud connection, select a customer-managed service account with only the roles
needed on that connection's declared resources. The connection journey gives the administrator
one exact IAM grant for the Uumi broker identity, verifies runtime and Secret Manager access,
and only then marks the connection ready. Customer identities are not Terraform inputs, so a new
connection never requires a Uumi redeployment. A browser worker receives an encrypted,
short-lived token for its selected secret-store connection only when Secure Capture or authorised
takeover needs it; the worker never receives impersonation permission. Uumi stores
`workload-identity://SERVICE_ACCOUNT_EMAIL` as the connection's authorisation reference; it is
identity metadata, not a credential. Uumi uses that selected identity for runtime,
secret-store, and connection-verification calls and rejects fallback to its own process identity.
The customer administrator applying the displayed grant must be authorised to update the selected
service account's IAM policy; Uumi cannot grant itself access to a customer account.

Register the customer-facing GitHub App with the Uumi ingestion URL ending in `/v1/github`,
the configured HTTPS URL as both the OAuth callback and post-install setup URL, read access to
secret scanning alerts, and the `secret_scanning_alert` event. Keep GitHub's automatic OAuth-on-
install option disabled: Uumi receives the installation first, then starts its PKCE-bound user
authorization automatically. Add the App OAuth client secret and webhook HMAC as Secret Manager
versions outside Terraform, then set their full immutable version references in the second-phase
variables. GitHub sends installation and installation-repository lifecycle events to Apps by
default; Uumi uses them to disable stale routing. Uumi never changes security settings on its
own source repository.

Email and password sign-in is enabled by Terraform. Enable Google sign-in in the Identity Platform
console. Any later enterprise SAML or OIDC client secrets stay in the provider configuration and
outside Terraform state.

## 3. Build and push every runtime by digest

`make images` builds:

| Image | Dockerfile | Runtime |
| --- | --- | --- |
| `api` | `server/api/Dockerfile` | Private control API |
| `publisher` | `server/publisher/Dockerfile` | Transactional outbox publisher |
| `ingestion` | `server/ingestion/Dockerfile` | Schedule, Secret Manager, GitHub, SCC, and provider intake |
| `broker` | `server/broker/Dockerfile` | Capability-scoped MCP broker |
| `coordinator` | `server/coordinator/Dockerfile` | Deterministic stage executor |
| `browser` | `server/browser/worker.Dockerfile` | One-run Playwright worker |
| `gateway` | `server/browser/Dockerfile` | IAP live view and takeover |
| `notification` | `server/notification/Dockerfile` | Durable email, Slack, and PagerDuty delivery |
| `auditlog` | `server/auditlog/Dockerfile` | Canonical audit delivery to locked Cloud Logging |

Tag each image with the Git commit, push it to the `image_repository` Terraform output, and
resolve its digest:

```bash
gcloud auth configure-docker REGION-docker.pkg.dev
docker tag uumi-api:local REGION-docker.pkg.dev/PROJECT/uumi/api:GIT_SHA
docker push REGION-docker.pkg.dev/PROJECT/uumi/api:GIT_SHA
gcloud artifacts docker images describe \
  REGION-docker.pkg.dev/PROJECT/uumi/api:GIT_SHA \
  --format='value(image_summary.digest)'
```

Repeat that operation for all nine images. Set every image variable to its full
`REGION-docker.pkg.dev/PROJECT/uumi/NAME@sha256:DIGEST` reference. Set the explicit capability
secret version and paired public key, notification application origin, notification secret
containers, at least one `workflow_organisation`, at least one IAP `gateway_user`, and the required
SCC, Secret Manager, provider, and recurring schedule sources.
Then apply again. For every organisation in `secret_sources`, configure relevant Google Secret
Manager secrets to publish to the `secret_topics` output; Terraform grants the Secret Manager
service agent publisher access to those topics.

The resulting graph includes:

- Cloud Workflows plus Eventarc/Pub/Sub delivery for authoritative run coordination.
- Private API, publisher, broker, coordinator, notification, and canonical audit Cloud Run services.
- Authenticated public transport for signed GitHub/provider webhooks and OIDC-bound Google push
  delivery.
- SCC v2 and Secret Manager topics, retrying push subscriptions, recurring Cloud Scheduler jobs,
  and retained dead-letter review.
- A no-public-IP, Shielded, CMEK-encrypted, auto-deleting Compute Engine VM per browser run.
- A private browser network and one-run VM template. Computer Use egress is intentionally disabled
  until Uumi can create and remove its allowlisted proxy for active browser sessions only.
- An enforced VPC Service Controls perimeter for persisted data APIs plus a project resource
  location policy bound to the selected region. When Agent Gateway is enabled, Vertex Agent
  Runtime is governed by Gateway, IAM, Model Armor, and CMEK instead of VPC Service Controls,
  because Google Cloud does not support attaching Agent Gateway to a VPC-SC Agent Runtime.
  Third-party internet control remains the Secure Web Proxy's responsibility.
- The signed-webhook ingestion service remains externally reachable by design. Cloud Run's Admin
  API is therefore outside the service perimeter; ingress authentication and replay protection
  guard that transport, while every persisted data service stays inside the perimeter. The Admin
  API remains reachable from the VPC so the broker can perform approved Cloud Run rotation steps.
- IAP-authenticated live view and takeover through a VPC-connected Cloud Run gateway.
- A one-year locked, versioned, CMEK evidence bucket containing sanitised replay and verified
  hash-chain manifests, plus a seven-year locked regional Cloud Logging bucket for canonical
  audit events.
- OpenTelemetry export to Cloud Trace and Monitoring with alerts on incident-ingestion,
  notification, and audit dead letters.

## 4. Deploy and register the four agents

Run the deployment under the `uumi-agents` deployment identity and use the Terraform outputs
for the staging bucket, KMS key, and two governed gateways:

```bash
uv run --all-extras python -m agents.deploy \
  --project YOUR_PROJECT \
  --organisation YOUR_UUMI_ORGANISATION \
  --region YOUR_REGION \
  --staging-bucket AGENT_STAGING_BUCKET \
  --kms-key AGENT_KMS_KEY \
  --ingress-gateway AGENT_INGRESS_GATEWAY \
  --egress-gateway AGENT_EGRESS_GATEWAY \
  --caller-role AGENT_CALLER_ROLE \
  --approved-caller serviceAccount:UUMI_API_SERVICE_ACCOUNT \
  --approved-caller serviceAccount:UUMI_COORDINATOR_SERVICE_ACCOUNT \
  --impersonate-service-account UUMI_AGENTS_SERVICE_ACCOUNT \
  --version RELEASE_VERSION
```

The deployment uploads the complete Python package topology, creates separately bounded
Inventory, Planner, Playbook Builder, and Console Operator ADK applications in Agent Runtime,
assigns each deployment its own Agent Identity, enables tracing and Memory Bank, binds ingress and
egress Agent Gateway enforcement, and writes immutable tenant routing registrations to Firestore.
Agent Runtime deployments are catalogued in Agent Registry; Firestore remains Uumi's exact
per-tenant and per-skill routing index.

## 5. Operational readiness

Before enabling schedules or webhooks, verify:

- Identity Platform completes email and Google sign-in on an authorised domain, and the
  API rejects a sign-in token issued for a different project;
- all nine Cloud Run revisions use the expected image digests;
- the four agent registrations report ready and resolve exactly one deployment per skill;
- each registration carries a distinct Agent Identity and both governed gateway resources;
- Model Armor blocks a seeded prompt-injection probe and IAP rejects an unregistered endpoint;
- capability, GitHub, and provider webhook secret versions exist and IAM grants are limited to
  their workloads;
- every workload-identity connection can impersonate only its selected customer service account,
  and a connection-scoped read fails when its required resource role is removed;
- a customer GitHub App installation completes PKCE user verification, receives a signed
  installation delivery, and reports secret scanning enabled for every selected repository;
- credential Controls pin verified-exposure sources independently of the GitHub connection and
  ambiguous repository correlations require confirmation;
- adding or removing an installation repository invalidates readiness until onboarding is repeated;
- Workflows can complete a controlled dry-run assignment in an isolated non-production
  environment;
- the browser VM has no external IP, starts the exact digest, and is deleted at run completion;
- the browser VM's default route resolves to Secure Web Proxy and an unlisted domain is denied;
- the service perimeter is enforced, the region policy matches deployment, and operator access
  succeeds only through the declared Access Context Manager level;
- IAP view and takeover are identity-bound and Secure Capture produces no secret-bearing frame;
- SCC and Secret Manager delivery failures appear in the retained dead-letter subscription;
- a seeded delivery failure opens the corresponding Cloud Monitoring incident and its traces
  correlate to the runtime revision;
- the final negative provider and secret probes pass and the exported audit manifest validates
  from the genesis hash.

No credential value is an infrastructure input. API-key and OAuth connection material is created
and governed in Secret Manager after the platform foundation exists; workload-identity
connections store only the selected service-account reference.

The storage module creates one CMEK-protected browser-session secret container per configured
Uumi organisation. The API can list and reconcile its versions, while the isolated browser
worker can only add and access versions. Connection setup selects no workload secret: the setup
worker writes filtered provider state to the organisation container and the API receives only the
resulting version reference and fingerprint.
