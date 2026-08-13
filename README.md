# FireKey

FireKey is an enterprise credential-rotation platform that discovers credential exposure, plans safe rotations, deploys replacement generations, verifies real application behaviour, obtains action-bound approval, revokes old credentials, and produces immutable evidence.

The product and end-to-end safety contract live in [`firekey.md`](firekey.md).

## Architecture

- `packages/contracts` contains canonical immutable models shared by every component.
- `packages/policy` contains deterministic policy and approval rules.
- `packages/telemetry` contains safe observability primitives.
- `packages/testkit` contains fakes and fixtures that cannot enter production deployments.
- `server/core` contains provider-independent state, workflow, storage, audit, and identity logic.
- `server/connectors` contains provider, secret-store, runtime, and incident adapters.
- `server/agents` contains the four separately deployed ADK agents.
- `server/api`, `server/broker`, `server/verifier`, `server/browser`, and `server/capture` are independent deployment boundaries.
- `infra` contains the authoritative Cloud Workflows and Terraform resources.

## Development

Python 3.12 and `uv` are required.

```bash
make sync
make verify
```

The repository is built feature by feature. Directories are added when they contain executable code, configuration, or tests; the target architecture is not represented by empty placeholders.

