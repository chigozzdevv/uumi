# FireKey infrastructure

FireKey uses a two-phase infrastructure deployment. The foundation is created first so images,
Secret Manager versions, organisation grants, ingestion sources, and IAP users can be supplied as real
immutable inputs. The second apply creates all seven runtimes, the Workflows coordinator, event
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
Keep all seven image variables and `capability_secret_version` null for the first apply.

```bash
terraform -chdir=infra/terraform/environments/dev init \
  -backend-config=bucket=YOUR_STATE_BUCKET \
  -backend-config=prefix=firekey/dev
terraform -chdir=infra/terraform/environments/dev apply \
  -var-file=values.tfvars
```

This creates the protected Firestore database, service accounts, immutable Artifact Registry,
CMEK keys, the locked evidence bucket, Agent Runtime staging bucket, GitHub and provider webhook secret
containers, capability secret container, private browser network, and one-run VM template.

Create secret versions outside Terraform. The capability secret version must contain exactly the
raw 32-byte private key of an Ed25519 keypair; `capability_public_key` contains only the paired raw
public key in unpadded base64url form. Only the API and coordinator can read the private key.
Broker, gateway, and one-run browser workers receive the public key and therefore cannot mint
capabilities. Each GitHub organisation and provider webhook requires a distinct random HMAC
secret version. Provider signatures cover `X-FireKey-Timestamp + "." + raw-body` and FireKey
rejects timestamps outside the configured replay window. Do not
place private or HMAC values in Terraform variables, plans, state, commands, or shell history.

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

Repeat that operation for all seven images. Set every image variable to its full
`REGION-docker.pkg.dev/PROJECT/firekey/NAME@sha256:DIGEST` reference. Set the explicit capability
secret version and paired public key, at least one `workflow_organisation`, at least one IAP
`gateway_user`, and the required SCC, Secret Manager, provider, and recurring schedule sources.
Then apply again. For every organisation in `secret_sources`, configure relevant Google Secret
Manager secrets to publish to the `secret_topics` output; Terraform grants the Secret Manager
service agent publisher access to those topics.

The resulting graph includes:

- Cloud Workflows plus Eventarc/Pub/Sub delivery for authoritative run coordination.
- Private API, publisher, broker, and coordinator Cloud Run services.
- Authenticated public transport for signed GitHub/provider webhooks and OIDC-bound Google push
  delivery.
- SCC v2 and Secret Manager topics, retrying push subscriptions, recurring Cloud Scheduler jobs,
  and retained dead-letter review.
- A no-public-IP, Shielded, CMEK-encrypted, auto-deleting Compute Engine VM per browser run.
- IAP-authenticated live view and takeover through a VPC-connected Cloud Run gateway.
- A one-year locked, versioned, CMEK evidence bucket containing sanitised replay and verified
  hash-chain manifests.

## 4. Deploy and register the four agents

Run the deployment under the `firekey-agents` deployment identity and use the Terraform outputs
for the staging bucket, KMS key, and two governed gateways:

```bash
uv run python -m agents.deploy \
  --project YOUR_PROJECT \
  --organisation YOUR_FIREKEY_ORGANISATION \
  --region YOUR_REGION \
  --staging-bucket AGENT_STAGING_BUCKET \
  --kms-key AGENT_KMS_KEY \
  --ingress-gateway AGENT_INGRESS_GATEWAY \
  --egress-gateway AGENT_EGRESS_GATEWAY \
  --approved-caller FIREKEY_COORDINATOR_SERVICE_ACCOUNT \
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

- all seven Cloud Run revisions use the expected image digests;
- the four agent registrations report ready and resolve exactly one deployment per skill;
- each registration carries a distinct Agent Identity and both governed gateway resources;
- Model Armor blocks a seeded prompt-injection probe and IAP rejects an unregistered endpoint;
- capability, GitHub, and provider webhook secret versions exist and IAM grants are limited to
  their workloads;
- Workflows can complete a controlled dry-run assignment in an isolated non-production
  environment;
- the browser VM has no external IP, starts the exact digest, and is deleted at run completion;
- IAP view and takeover are identity-bound and Secure Capture produces no secret-bearing frame;
- SCC and Secret Manager delivery failures appear in the retained dead-letter subscription;
- the final negative provider and secret probes pass and the exported audit manifest validates
  from the genesis hash.

No credential value is an infrastructure input. Provider and runtime connection secrets are
created and governed in Secret Manager after the platform foundation exists.
