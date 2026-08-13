# FireKey infrastructure

FireKey uses a two-phase infrastructure deployment. The foundation is created first so images,
Secret Manager versions, organisation grants, SCC sources, and IAP users can be supplied as real
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
CMEK keys, the locked evidence bucket, Agent Runtime staging bucket, GitHub webhook secret
containers, capability secret container, private browser network, and one-run VM template.

Create secret versions outside Terraform. The capability secret version must contain exactly the
raw 32-byte private key of an Ed25519 keypair; `capability_public_key` contains only the paired raw
public key in unpadded base64url form. Only the API and coordinator can read the private key.
Broker, gateway, and one-run browser workers receive the public key and therefore cannot mint
capabilities. Each GitHub organisation requires a distinct random HMAC secret version. Do not
place private or HMAC values in Terraform variables, plans, state, commands, or shell history.

## 3. Build and push every runtime by digest

`make images` builds:

| Image | Dockerfile | Runtime |
| --- | --- | --- |
| `api` | `server/api/Dockerfile` | Private control API |
| `publisher` | `server/publisher/Dockerfile` | Transactional outbox publisher |
| `ingestion` | `server/ingestion/Dockerfile` | GitHub and SCC intake |
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
`gateway_user`, and each SCC source's Cloud organisation, location, and filter. Then apply again.

The resulting graph includes:

- Cloud Workflows plus Eventarc/Pub/Sub delivery for authoritative run coordination.
- Private API, publisher, broker, and coordinator Cloud Run services.
- Authenticated public transport only for GitHub HMAC and SCC OIDC ingestion.
- SCC v2 notification configurations, retrying push subscriptions, and retained dead-letter
  review.
- A no-public-IP, Shielded, CMEK-encrypted, auto-deleting Compute Engine VM per browser run.
- IAP-authenticated live view and takeover through a VPC-connected Cloud Run gateway.
- A one-year locked, versioned, CMEK evidence bucket containing sanitised replay and verified
  hash-chain manifests.

## 4. Deploy and register the four agents

Use the Terraform outputs for the staging bucket, KMS key, and `firekey-agents` service account:

```bash
uv run python -m agents.deploy \
  --project YOUR_PROJECT \
  --organisation YOUR_FIREKEY_ORGANISATION \
  --region YOUR_REGION \
  --staging-bucket AGENT_STAGING_BUCKET \
  --service-account FIREKEY_AGENT_SERVICE_ACCOUNT \
  --kms-key AGENT_KMS_KEY \
  --version RELEASE_VERSION
```

The deployment uploads the complete Python package topology, creates separately bounded
Inventory, Planner, Playbook Builder, and Console Operator ADK applications in Agent Runtime,
enables tracing and Memory Bank, and writes immutable tenant routing registrations to Firestore.
Agent Runtime deployments are automatically visible in Google Agent Registry; Firestore remains
FireKey's exact per-tenant and per-skill routing index.

## 5. Operational readiness

Before enabling schedules or webhooks, verify:

- all seven Cloud Run revisions use the expected image digests;
- the four agent registrations report ready and resolve exactly one deployment per skill;
- capability and GitHub secret versions exist and IAM grants are limited to their workloads;
- Workflows can complete a controlled dry-run assignment in an isolated non-production
  environment;
- the browser VM has no external IP, starts the exact digest, and is deleted at run completion;
- IAP view and takeover are identity-bound and Secure Capture produces no secret-bearing frame;
- SCC delivery failures appear in the retained dead-letter subscription;
- the final negative provider and secret probes pass and the exported audit manifest validates
  from the genesis hash.

No credential value is an infrastructure input. Provider and runtime connection secrets are
created and governed in Secret Manager after the platform foundation exists.
