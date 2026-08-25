import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import google.auth
import vertexai
from connectors.google import GoogleRestClient
from contracts import AgentKind, AgentRegistration, AgentStatus
from core.errors import ResourceNotFoundError
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.cloud.firestore_v1 import AsyncClient
from vertexai import types

from agents.fleet import _SKILLS, AgentFleetService
from agents.storage import AgentRepository

_ROOT = Path(__file__).resolve().parents[2]
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


async def deploy(
    project_id: str,
    organisation_id: str,
    region: str,
    staging_bucket: str,
    kms_key: str,
    ingress_gateway: str,
    egress_gateway: str,
    caller_role: str,
    approved_callers: frozenset[str],
    version: str,
    credentials: Credentials | None = None,
) -> tuple[AgentRegistration, ...]:
    google = GoogleRestClient(credentials=credentials)
    try:
        return await _deploy_fleet(
            google,
            project_id,
            organisation_id,
            region,
            staging_bucket,
            kms_key,
            ingress_gateway,
            egress_gateway,
            caller_role,
            approved_callers,
            version,
            credentials,
        )
    finally:
        await google.close()


async def _deploy_fleet(
    google: GoogleRestClient,
    project_id: str,
    organisation_id: str,
    region: str,
    staging_bucket: str,
    kms_key: str,
    ingress_gateway: str,
    egress_gateway: str,
    caller_role: str,
    approved_callers: frozenset[str],
    version: str,
    credentials: Credentials | None = None,
) -> tuple[AgentRegistration, ...]:
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = region
    client = vertexai.Client(
        project=project_id,
        location=region,
        http_options={"api_version": "v1beta1"},
        credentials=credentials,
    )
    repository = AgentRepository(AsyncClient(project=project_id, credentials=credentials))
    fleet = AgentFleetService(repository)
    registrations = []
    for kind in AgentKind:
        registration_id = f"{kind.value}_{version.replace('.', '_')}"
        try:
            current = await repository.get(organisation_id, registration_id)
        except ResourceNotFoundError:
            pass
        else:
            if (
                current.kind is not kind
                or current.version != version
                or current.region != region
                or current.ingress_gateway != ingress_gateway
                or current.egress_gateway != egress_gateway
                or current.approved_callers != approved_callers
                or current.status is not AgentStatus.READY
            ):
                raise RuntimeError(
                    f"existing {kind.value} registration does not match this deployment"
                )
            registrations.append(current)
            await _grant_callers(
                google,
                region,
                current.deployment,
                caller_role,
                approved_callers,
            )
            continue
        module = __import__(f"agents.{kind.value}.agent", fromlist=["agent_app"])
        app = module.agent_app
        remote = client.agent_engines.create(
            agent=app,
            config=_deployment_config(
                project_id,
                kind,
                version,
                staging_bucket,
                kms_key,
                ingress_gateway,
                egress_gateway,
            ),
        )
        resource = remote.api_resource
        if resource is None or not resource.name:
            raise RuntimeError(f"Agent Runtime returned no resource for {kind.value}")
        identity = _effective_identity(resource)
        await _grant_callers(
            google,
            region,
            resource.name,
            caller_role,
            approved_callers,
        )
        registration = AgentRegistration(
            id=registration_id,
            organisation_id=organisation_id,
            kind=kind,
            display_name=f"Uumi {kind.value.title()} Agent",
            version=version,
            skills=_SKILLS[kind],
            owner="Uumi Platform",
            identity=identity,
            endpoint=f"https://{region}-aiplatform.googleapis.com",
            deployment=resource.name,
            registry=f"//agentregistry.googleapis.com/projects/{project_id}/locations/{region}",
            ingress_gateway=ingress_gateway,
            egress_gateway=egress_gateway,
            region=region,
            approved_callers=approved_callers,
            tool_destinations=frozenset({"firestore"}),
            status=AgentStatus.READY,
            registered_at=datetime.now(UTC),
        )
        registrations.append(await fleet.register(registration))
    return tuple(registrations)


def _deployment_config(
    project_id: str,
    kind: AgentKind,
    version: str,
    staging_bucket: str,
    kms_key: str,
    ingress_gateway: str,
    egress_gateway: str,
) -> dict[str, Any]:
    return {
        "display_name": f"Uumi {kind.value.title()} Agent {version}",
        "description": f"Uumi managed {kind.value} agent",
        "staging_bucket": staging_bucket,
        "requirements": str(_ROOT / "server" / "agents" / "requirements.txt"),
        "extra_packages": [
            str(_ROOT / "server"),
            str(_ROOT / "packages" / "contracts" / "src"),
            str(_ROOT / "packages" / "policy" / "src"),
            str(_ROOT / "packages" / "telemetry" / "src"),
        ],
        "env_vars": {"GOOGLE_CLOUD_PROJECT": project_id},
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "agent_gateway_config": {
            "client_to_agent_config": {"agent_gateway": ingress_gateway},
            "agent_to_anywhere_config": {"agent_gateway": egress_gateway},
        },
        "min_instances": 0,
        "max_instances": 10,
        "resource_limits": {"cpu": "2", "memory": "4Gi"},
        "container_concurrency": 5,
        "encryption_spec": {"kms_key_name": kms_key},
        "labels": {
            "uumi-agent": kind.value,
            "uumi-version": version.replace(".", "-"),
        },
        "context_spec": {
            "memory_bank_config": {
                "ttl_config": {"default_ttl": "2592000s"},
                "disable_memory_revisions": False,
            }
        },
    }


def _effective_identity(resource: Any) -> str:
    spec = getattr(resource, "spec", None)
    identity = getattr(spec, "effective_identity", None)
    if not isinstance(identity, str) or not identity.startswith("principal://"):
        raise RuntimeError("Agent Runtime returned no managed Agent Identity")
    return identity


async def _grant_callers(
    google: GoogleRestClient,
    region: str,
    deployment: str,
    role: str,
    callers: frozenset[str],
) -> None:
    project = deployment.split("/", 2)[1] if deployment.startswith("projects/") else ""
    if role != f"projects/{project}/roles/uumiAgentCaller":
        raise ValueError("caller role must be the Uumi least-privilege project role")
    if not callers or any(not _iam_member(value) for value in callers):
        raise ValueError("approved callers must be explicit IAM service-account or group members")
    endpoint = f"https://{region}-aiplatform.googleapis.com/v1beta1/{deployment}"
    policy = await google.request("POST", f"{endpoint}:getIamPolicy", json={})
    bindings = policy.get("bindings", [])
    if not isinstance(bindings, list):
        raise RuntimeError("Agent Runtime returned an invalid IAM policy")
    changed: list[dict[str, Any]] = []
    found = False
    for binding in bindings:
        if not isinstance(binding, dict):
            raise RuntimeError("Agent Runtime returned an invalid IAM binding")
        if binding.get("role") != role:
            changed.append(binding)
            continue
        members = binding.get("members", [])
        if not isinstance(members, list):
            raise RuntimeError("Agent Runtime returned an invalid IAM member list")
        if found:
            raise RuntimeError("Agent Runtime returned duplicate Uumi caller bindings")
        changed.append({**binding, "members": sorted(callers)})
        found = True
    if not found:
        changed.append({"role": role, "members": sorted(callers)})
    if changed == bindings:
        return
    updated: dict[str, Any] = {"bindings": changed}
    if isinstance(policy.get("etag"), str):
        updated["etag"] = policy["etag"]
    await google.request(
        "POST",
        f"{endpoint}:setIamPolicy",
        json={"policy": updated},
    )


def _iam_member(value: str) -> bool:
    kind, separator, identifier = value.partition(":")
    return bool(separator and identifier and kind in {"serviceAccount", "group"})


def _deployment_credentials(service_account: str | None) -> Credentials | None:
    if service_account is None:
        return None
    source, _ = google.auth.default(scopes=(_CLOUD_PLATFORM_SCOPE,))
    credential_factory: Any = impersonated_credentials.Credentials
    return cast(
        Credentials,
        credential_factory(
            source_credentials=source,
            target_principal=service_account,
            target_scopes=(_CLOUD_PLATFORM_SCOPE,),
            lifetime=3600,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--organisation", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--kms-key", required=True)
    parser.add_argument("--ingress-gateway", required=True)
    parser.add_argument("--egress-gateway", required=True)
    parser.add_argument("--caller-role", required=True)
    parser.add_argument("--approved-caller", required=True, action="append")
    parser.add_argument("--version", required=True)
    parser.add_argument("--impersonate-service-account")
    args = parser.parse_args()
    import asyncio

    values = asyncio.run(
        deploy(
            args.project,
            args.organisation,
            args.region,
            args.staging_bucket,
            args.kms_key,
            args.ingress_gateway,
            args.egress_gateway,
            args.caller_role,
            frozenset(args.approved_caller),
            args.version,
            _deployment_credentials(args.impersonate_service_account),
        )
    )
    print(json.dumps([value.model_dump(mode="json") for value in values]))


if __name__ == "__main__":
    main()
