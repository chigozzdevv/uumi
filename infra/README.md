# FireKey infrastructure

FireKey infrastructure is split between a one-time remote-state bootstrap and independently deployable environments.

## Bootstrap state

```bash
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap apply \
  -var=project_id=YOUR_PROJECT \
  -var=bucket_name=YOUR_STATE_BUCKET
```

The state bucket has versioning, public-access prevention, uniform access, and destruction protection.

## Initialise the development environment

```bash
terraform -chdir=infra/terraform/environments/dev init \
  -backend-config=bucket=YOUR_STATE_BUCKET \
  -backend-config=prefix=firekey/dev
terraform -chdir=infra/terraform/environments/dev plan \
  -var-file=values.tfvars
```

`us-east1` is the default shared region for the current Agent Runtime, Sessions, Memory Bank, and Agent Gateway feature set. Change it only to another region accepted by the environment validation after confirming every selected platform feature remains available there.

No credentials or secret values belong in Terraform variables, state, plans, or outputs.

## Deploy the API by digest

The first environment apply uses `api_image = null`. It provisions the protected Firestore database, workload identities, organisation grants, and immutable Artifact Registry repository without creating a Cloud Run revision that references a missing image.

Build and push a unique commit tag after that apply:

```bash
gcloud auth configure-docker REGION-docker.pkg.dev
docker build \
  --file server/api/Dockerfile \
  --tag REGION-docker.pkg.dev/PROJECT/firekey/api:GIT_SHA \
  .
docker push REGION-docker.pkg.dev/PROJECT/firekey/api:GIT_SHA
gcloud artifacts docker images describe \
  REGION-docker.pkg.dev/PROJECT/firekey/api:GIT_SHA \
  --format='value(image_summary.digest)'
```

Set `api_image` to the returned full image name with its `@sha256:...` digest and apply the environment again. Tags are immutable, but Cloud Run also requires the digest in Terraform so a later tag cannot change a deployed revision.

The API accepts internal ingress only. Workflows in the same project can reach it through the default `run.app` URL, and only the workflow service account receives `roles/run.invoker`.
