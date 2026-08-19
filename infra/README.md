# FireKey infrastructure

FireKey uses a two-phase infrastructure deployment. The foundation is created first so images,
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
  -backend-config=prefix=firekey/dev
terraform -chdir=infra/terraform/environments/dev apply \
  -var-file=values.tfvars
```

This creates the protected Firestore database, service accounts, immutable Artifact Registry,
CMEK keys, locked evidence and audit storage, Agent Runtime staging bucket, GitHub App OAuth and
webhook secret containers, provider webhook secret containers, capability secret container,
service perimeter, regional policy,
private browser network, Secure Web Proxy, one-run VM template, and Identity Platform sign-in
configuration (email and password enabled; `identity_platform_domains` admits the client origin).

Create secret versions outside Terraform. The capability secret version must contain exactly the
raw 32-byte private key of an Ed25519 keypair; `capability_public_key` contains only the paired raw
public key in unpadded base64url form. Only the API and coordinator can read the private key.
Broker, gateway, and one-run browser workers receive the public key and therefore cannot mint
capabilities. The GitHub App uses one random webhook HMAC secret and one OAuth client secret;
customer installations are mapped to FireKey organisations in Firestore only after a signed
installation delivery and a PKCE-bound user authorization prove ownership. Provider webhooks use
a distinct random HMAC secret per configured source. Provider signatures cover
`X-FireKey-Timestamp + "." + raw-body` and FireKey
rejects timestamps outside the configured replay window. Do not
place private or HMAC values in Terraform variables, plans, state, commands, or shell history.

Register the customer-facing GitHub App with the FireKey ingestion URL ending in `/v1/github`,
the configured HTTPS callback URL, read access to secret scanning alerts, and the
`secret_scanning_alert` event. Add the App OAuth client secret and webhook HMAC as Secret Manager
versions outside Terraform, then set their full immutable version references in the second-phase
variables. GitHub sends installation and installation-repository lifecycle events to Apps by
default; FireKey uses them to disable stale routing. FireKey never changes security settings on its
own source repository.

Enable the Google and GitHub sign-in providers in the Identity Platform console, never in
Terraform. The GitHub provider needs an OAuth application's client secret; record the OAuth
application's client ID only, and let the console hold the secret.
The same rule applies to any later enterprise SAML or OIDC client secrets.

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
docker tag firekey-api:local REGION-docker.pkg.dev/PROJECT/firekey/api:GIT_SHA
docker push REGION-docker.pkg.dev/PROJECT/firekey/api:GIT_SHA
gcloud artifacts docker images describe \
  REGION-docker.pkg.dev/PROJECT/firekey/api:GIT_SHA \
  --format='value(image_summary.digest)'
```

Repeat that operation for all nine images. Set every image variable to its full
`REGION-docker.pkg.dev/PROJECT/firekey/NAME@sha256:DIGEST` reference. Set the explicit capability
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
- A regional next-hop Secure Web Proxy that admits the browser VM only by workload identity and
  its exact approved domains. Cloud Run uses a separate source subnet and a fixed Google and
  connector-domain list because Direct VPC egress does not propagate service-account identity to
  Secure Web Proxy; unmatched internet egress is denied.
- An enforced VPC Service Controls perimeter for supported Google APIs plus a project resource
  location policy bound to the selected region. Third-party internet control remains the Secure
  Web Proxy's responsibility.
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

Run the deployment under the `firekey-agents` deployment identity and use the Terraform outputs
for the staging bucket, KMS key, and two governed gateways:

```bash
uv run --all-extras python -m agents.deploy \
  --project YOUR_PROJECT \
  --organisation YOUR_FIREKEY_ORGANISATION \
  --region YOUR_REGION \
  --staging-bucket AGENT_STAGING_BUCKET \
  --kms-key AGENT_KMS_KEY \
  --ingress-gateway AGENT_INGRESS_GATEWAY \
  --egress-gateway AGENT_EGRESS_GATEWAY \
  --caller-role AGENT_CALLER_ROLE \
  --approved-caller serviceAccount:FIREKEY_API_SERVICE_ACCOUNT \
  --approved-caller serviceAccount:FIREKEY_COORDINATOR_SERVICE_ACCOUNT \
  --version RELEASE_VERSION
```

The deployment uploads the complete Python package topology, creates separately bounded
Inventory, Planner, Playbook Builder, and Console Operator ADK applications in Agent Runtime,
assigns each deployment its own Agent Identity, enables tracing and Memory Bank, binds ingress and
egress Agent Gateway enforcement, and writes immutable tenant routing registrations to Firestore.
Agent Runtime deployments are catalogued in Agent Registry; Firestore remains FireKey's exact
per-tenant and per-skill routing index.

## 5. Operational readiness

Before enabling schedules or webhooks, verify:

- Identity Platform completes email, Google, and GitHub sign-in on an authorised domain, and the
  API rejects a sign-in token issued for a different project;
- all nine Cloud Run revisions use the expected image digests;
- the four agent registrations report ready and resolve exactly one deployment per skill;
- each registration carries a distinct Agent Identity and both governed gateway resources;
- Model Armor blocks a seeded prompt-injection probe and IAP rejects an unregistered endpoint;
- capability, GitHub, and provider webhook secret versions exist and IAM grants are limited to
  their workloads;
- a customer GitHub App installation completes PKCE user verification, receives a signed
installation delivery, reports secret scanning enabled for every selected repository, and maps
each repository to exactly one managed credential;
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

No credential value is an infrastructure input. Provider and runtime connection secrets are
created and governed in Secret Manager after the platform foundation exists.

For browser connection setup, grant the FireKey API service account version-list access and the
isolated browser worker service account `roles/secretmanager.secretVersionAdder` only on the
chosen session secret container. The setup worker writes the filtered browser state directly to
that container; the API receives only the resulting version reference and fingerprint.
