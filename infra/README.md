# Uumi infrastructure

Terraform provisions the Uumi data project, its protected runtime graph, and the optional
Agent Runtime project. Deployment has two applies:

| Phase | Creates | Gate |
| --- | --- | --- |
| Foundation | State, Firestore, KMS, Secret Manager containers, IAM, storage, identity, network, perimeter inputs, and Artifact Registry | Runtime image references remain `null` |
| Runtime | Cloud Run services, Workflows, event delivery, browser gateway, and agent-facing controls | Required image digests, secret versions, organisations, policy, and integration inputs are present |

The data project owns Uumi state and services. When `agent_project_id` differs from `project_id`,
the agent project owns Agent Runtime, Agent Gateway, Agent Registry, Model Armor, staging storage,
and agent CMEK resources. Terraform receives resource names and immutable version references;
credential values stay outside Terraform.

## Prerequisites

- A Google Cloud project with billing and the permissions required by the selected IAM and
  organisation policies.
- Terraform 1.15.x (CI uses 1.15.8), the Google Cloud CLI, and Application Default Credentials.
- Docker, `uv`, Python 3.12, and Node.js/npm for image builds and agent deployment.
- An organisation Access Context Manager policy and an existing operator access level for the
  runtime phase.

Validate the Terraform roots from the repository root before applying:

```sh
make infra TERRAFORM=terraform
```

## 1. Prepare deployment values

Copy the example file to the ignored environment file:

```sh
cp infra/terraform/environments/dev/values.example.tfvars \
  infra/terraform/environments/dev/values.tfvars
```

Replace every `replace-with-*` value. Keep `values.tfvars` out of Git. The important inputs are:

| Input group | Variables |
| --- | --- |
| Projects and location | `project_id`, optional `agent_project_id`, `region`, `agent_model_location`, `zone` |
| Runtime admission | `workflow_organisations`, `access_policy_id`, `operator_access_level`, `gateway_users` |
| Egress allowlists | `browser_allowed_domains`, `runtime_connector_domains` |
| Identity Platform | `identity_platform_domains`, `oidc_audience` |
| Event sources | `scc_sources`, `secret_sources`, `provider_sources`, `rotation_schedules` |
| Runtime images | The ten required image variables listed in [Build and publish](#5-build-and-publish-images) |

For the foundation apply, leave image references and optional integration values `null` as shown
in `values.example.tfvars`. The runtime preconditions require the complete set before any
automation image can be deployed.

## 2. Bootstrap remote state

Create the state bucket once:

```sh
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap apply \
  -var=project_id=YOUR_PROJECT \
  -var=bucket_name=YOUR_STATE_BUCKET
```

The bucket has versioning, uniform bucket-level access, public-access prevention, a 90-day rule
for archived objects, and `prevent_destroy`.

## 3. Apply the foundation

Initialise the environment against the state bucket:

```sh
terraform -chdir=infra/terraform/environments/dev init \
  -backend-config=bucket=YOUR_STATE_BUCKET \
  -backend-config=prefix=uumi/dev
terraform -chdir=infra/terraform/environments/dev apply \
  -var-file=values.tfvars
```

The foundation creates:

- Firestore Native with pessimistic concurrency, point-in-time recovery, and delete protection.
- KMS keys, the Artifact Registry, locked evidence and audit storage, agent staging storage, and
  organisation-scoped browser-session secret containers.
- Separate service accounts for the API, web gateway, event delivery, ingestion, publisher,
  broker, coordinator, browser worker, gateway, agents, notifications, audit, and demo consumer.
- Identity Platform configuration, browser VPC/subnets, the one-run Compute Engine template,
  service-perimeter inputs, Model Armor templates, and event-source containers.

No runtime service is admitted by this apply. The Cloud Run graph and Workflows become enabled only
after the image and control gates pass in the runtime apply.

## 4. Create secret versions outside Terraform

Create the following Secret Manager versions with the Google Cloud CLI or an approved secret
provisioning process. Do not place their values in `values.tfvars`, Terraform plans, state,
commands, logs, or shell history.

| Secret | Terraform input | Boundary |
| --- | --- | --- |
| Capability signing key | `capability_secret_version` | Exactly the raw 32-byte Ed25519 private key. Only the API and coordinator read it. |
| Capability public key | `capability_public_key` | The paired raw public key, unpadded base64url. Broker, gateway, and browser workers receive only this key. |
| GitHub App OAuth and webhook HMAC | `github_client_secret_version`, `github_webhook_secret_version` | Immutable versions. Use one random webhook HMAC secret for the App. |
| Google Cloud OAuth client secret | `google_cloud_client_secret_version` | Immutable version. Set the client ID, secret version, and callback URL together. |
| Email delivery credential | `notification_email_secret_version` | Must belong to one entry in `notification_secrets`; set it with `notification_email_sender`. |
| Provider webhook HMAC | Provider secret containers | Use a distinct random HMAC secret per source. Signatures cover `X-Uumi-Timestamp + "." + raw-body`. |

The capability public key must be the pair for the private key. Terraform validates the immutable
version shape; IAM grants must cover the project that owns each referenced secret. Customer
workload credentials are created and rotated after the foundation exists; Uumi stores only their
version references and fingerprints.

## 5. Build and publish images

Build from the repository root:

```sh
make images
```

The runtime phase requires these ten immutable image references. `demo_image` is optional and
deploys the Resend demo consumer when supplied.

| Variable | Dockerfile | Runtime |
| --- | --- | --- |
| `api_image` | `server/api/Dockerfile` | Private control API |
| `web_image` | `server/web/Dockerfile` | Authenticated web gateway |
| `publisher_image` | `server/publisher/Dockerfile` | Transactional outbox publisher |
| `ingestion_image` | `server/ingestion/Dockerfile` | Schedule, Secret Manager, GitHub, SCC, and provider intake |
| `broker_image` | `server/broker/Dockerfile` | Capability-scoped MCP broker |
| `coordinator_image` | `server/coordinator/Dockerfile` | Deterministic stage executor and browser-session manager |
| `browser_image` | `server/browser/worker.Dockerfile` | One-run Computer Use VM worker image |
| `gateway_image` | `server/browser/Dockerfile` | IAP live view and takeover gateway |
| `notification_image` | `server/notification/Dockerfile` | Durable email, Slack, and PagerDuty delivery |
| `auditlog_image` | `server/auditlog/Dockerfile` | Canonical audit delivery to locked Cloud Logging |

Tag and push each image by Git commit, then resolve its digest. Example:

```sh
gcloud auth configure-docker REGION-docker.pkg.dev
docker tag uumi-api:local REGION-docker.pkg.dev/PROJECT/uumi/api:GIT_SHA
docker push REGION-docker.pkg.dev/PROJECT/uumi/api:GIT_SHA
gcloud artifacts docker images describe \
  REGION-docker.pkg.dev/PROJECT/uumi/api:GIT_SHA \
  --format='value(image_summary.digest)'
```

Set every required `*_image` variable to the complete
`REGION-docker.pkg.dev/PROJECT/uumi/NAME@sha256:DIGEST` value. A tag without a digest fails the
Terraform validation.

## 6. Configure external integrations

Complete these registrations before the runtime apply:

**Google Cloud onboarding**

- Register one HTTPS OAuth web client with a callback ending in `?google_cloud=callback`.
- Store its client secret as an immutable version and set
  `google_cloud_client_id`, `google_cloud_client_secret_version`, and
  `google_cloud_callback_url` together.
- The onboarding token is KMS-encrypted for a 15-minute session, never returned to the dashboard,
  and cleared after authorisation.

**GitHub App**

- Register the Uumi ingestion URL ending in `/v1/github`.
- Use the configured HTTPS URL for the OAuth callback and post-install setup URL.
- Grant read access to secret-scanning alerts and enable the `secret_scanning_alert` event.
- Disable automatic OAuth-on-install. Uumi verifies the installation delivery before starting the
  PKCE-bound user authorisation.
- Store the App OAuth secret and webhook HMAC as immutable versions.

**Customer Google Cloud connections**

- Select a dedicated customer-managed service account per connection.
- Grant it Cloud Run Developer, Secret Manager Viewer, Secret Manager Secret Version Manager, and
  Service Account User on the discovered Cloud Run identities as required by the connection.
- Uumi stores `workload-identity://SERVICE_ACCOUNT_EMAIL`, not a credential. Access is verified
  before the connection becomes ready.

**Identity Platform and notifications**

- Email/password sign-in is configured by Terraform. Enable Google sign-in in the Identity
  Platform console.
- Set `notification_app_url`, `notification_email_secret_version`, and
  `notification_email_sender` together when email delivery is enabled.

## 7. Apply the runtime graph

The second apply is a hard gate. It requires both control-plane images, all eight automation
images, the capability version and public key, organisation and policy inputs, browser/API
allowlists, IAP users, and the required GitHub and notification inputs.

```sh
terraform -chdir=infra/terraform/environments/dev apply \
  -var-file=values.tfvars
```

This enables nine Cloud Run services when all required images are supplied: API, web gateway,
publisher, ingestion, broker, coordinator, notification worker, audit publisher, and IAP browser
gateway. The browser worker image is used by the one-run Compute Engine VM template. The optional
Resend demo is a tenth Cloud Run service.

The graph also includes:

- Workflows, Eventarc, Pub/Sub, Cloud Scheduler, SCC notifications, ordered delivery, retries,
  and retained dead-letter subscriptions.
- Private runtime subnets, no-public-IP Shielded browser VMs, CMEK, and on-demand default-deny
  Secure Web Proxy egress for approved browser domains.
- VPC Service Controls and regional resource-location policy for supported persisted data APIs.
  Agent Gateway, IAM, Model Armor, and CMEK govern Agent Runtime when it is in a separate project.
- IAP-authenticated live view and takeover, locked regional audit logging, evidence retention, and
  Cloud Trace/Monitoring alerts.

## 8. Deploy and register the agent fleet

Run the deployment under the `uumi-agents` identity. From the repository root:

```sh
uv run --all-extras python -m agents.deploy \
  --project YOUR_AGENT_PROJECT \
  --catalog-project YOUR_UUMI_DATA_PROJECT \
  --organisation YOUR_UUMI_ORGANISATION \
  --region YOUR_REGION \
  --model-location YOUR_MODEL_LOCATION \
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

The command deploys four separately bounded ADK applications: Inventory Assessment, Planner,
Playbook Builder, and Console Operator. Each gets its own Agent Identity, Agent Runtime version,
gateway enforcement, tracing, and Memory Bank configuration. Immutable per-organisation routing
registrations are written to Firestore and catalogued in Agent Registry.

## 9. Readiness checks

Before enabling schedules or webhooks, verify:

- All required Cloud Run revisions use the expected image digests.
- The four agent registrations are ready and resolve exactly one deployment per skill.
- Capability, GitHub, provider webhook, and notification secret versions exist with workload-only
  IAM grants.
- Identity Platform sign-in works on an authorised domain and rejects tokens from another project.
- Every workload-identity connection is limited to its selected customer service account.
- GitHub installation, PKCE authorisation, secret-scanning delivery, and repository routing pass.
- Workflows completes a controlled dry run in an isolated non-production organisation.
- Browser VMs have no external IP, run the expected image, resolve egress through Secure Web Proxy,
  and are deleted after the run.
- IAP view/takeover is identity-bound and Secure Capture produces no secret-bearing frame.
- SCC, Secret Manager, notification, and audit dead-letter paths raise their Monitoring alerts.
- Negative provider and Secret Manager probes pass; the exported audit manifest validates from the
  genesis hash.

## Security invariants

- Plaintext credential values never enter Terraform, plans, state, logs, traces, evidence, replay,
  prompts, model context, or audit payloads.
- API and coordinator can read the capability private key. Broker, gateway, agents, and browser
  workers receive only the public key or short-lived, connection-scoped material when required.
- Provider and Secret Manager mutations use leases, revisions, fencing tokens, idempotency keys,
  and pre-mutation reconciliation. Stale workers cannot commit.
- Provider-side revocation is proved before the old Secret Manager version is disabled. A
  compensating recovery remains a separate terminal outcome.

## Outputs and changes

Useful outputs after the runtime apply include:

```sh
terraform -chdir=infra/terraform/environments/dev output
```

This exposes service URIs, the Artifact Registry prefix, event topics, dead-letter subscriptions,
the audit bucket, agent gateways, and the browser template. It does not expose secret values.

Keep `values.tfvars`, Terraform state, plans, and generated credentials outside Git. Review the
plan for unexpected IAM, perimeter, network, or retention changes before every apply.
