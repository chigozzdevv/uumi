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
  -var=project_id=YOUR_PROJECT
```

`us-east1` is the default shared region for the current Agent Runtime, Sessions, Memory Bank, and Agent Gateway feature set. Change it only to another region accepted by the environment validation after confirming every selected platform feature remains available there.

No credentials or secret values belong in Terraform variables, state, plans, or outputs.
