import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import vertexai
from contracts import AgentKind, AgentRegistration, AgentStatus
from core.errors import ResourceNotFoundError
from google.cloud.firestore_v1 import AsyncClient
from vertexai import types

from agents.fleet import _SKILLS, AgentFleetService
from agents.storage import AgentRepository

_ROOT = Path(__file__).resolve().parents[2]


async def deploy(
    project_id: str,
    organisation_id: str,
    region: str,
    staging_bucket: str,
    kms_key: str,
    ingress_gateway: str,
    egress_gateway: str,
    approved_caller: str,
    version: str,
) -> tuple[AgentRegistration, ...]:
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = region
    client = vertexai.Client(
        project=project_id,
        location=region,
        http_options={"api_version": "v1beta1"},
    )
    repository = AgentRepository(AsyncClient(project=project_id))
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
                or current.approved_callers != frozenset({approved_caller})
                or current.status is not AgentStatus.READY
            ):
                raise RuntimeError(
                    f"existing {kind.value} registration does not match this deployment"
                )
            registrations.append(current)
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
        registration = AgentRegistration(
            id=registration_id,
            organisation_id=organisation_id,
            kind=kind,
            display_name=f"FireKey {kind.value.title()} Agent",
            version=version,
            skills=_SKILLS[kind],
            owner="FireKey Platform",
            identity=identity,
            endpoint=f"https://{region}-aiplatform.googleapis.com",
            deployment=resource.name,
            registry=f"//agentregistry.googleapis.com/projects/{project_id}/locations/{region}",
            ingress_gateway=ingress_gateway,
            egress_gateway=egress_gateway,
            region=region,
            approved_callers=frozenset({approved_caller}),
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
        "display_name": f"FireKey {kind.value.title()} Agent {version}",
        "description": f"FireKey managed {kind.value} agent",
        "staging_bucket": staging_bucket,
        "requirements": str(_ROOT / "server" / "agents" / "requirements.txt"),
        "extra_packages": [
            str(_ROOT / "server"),
            str(_ROOT / "packages" / "contracts" / "src"),
            str(_ROOT / "packages" / "policy" / "src"),
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
            "firekey-agent": kind.value,
            "firekey-version": version.replace(".", "-"),
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--organisation", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--kms-key", required=True)
    parser.add_argument("--ingress-gateway", required=True)
    parser.add_argument("--egress-gateway", required=True)
    parser.add_argument("--approved-caller", required=True)
    parser.add_argument("--version", required=True)
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
            args.approved_caller,
            args.version,
        )
    )
    print(json.dumps([value.model_dump(mode="json") for value in values]))


if __name__ == "__main__":
    main()
