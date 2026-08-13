import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import vertexai
from contracts import AgentKind, AgentRegistration, AgentStatus
from google.cloud.firestore_v1 import AsyncClient

from agents.fleet import _SKILLS, AgentFleetService
from agents.storage import AgentRepository

_ROOT = Path(__file__).resolve().parents[2]


async def deploy(
    project_id: str,
    organisation_id: str,
    region: str,
    staging_bucket: str,
    service_account: str,
    kms_key: str,
    version: str,
) -> tuple[AgentRegistration, ...]:
    client = vertexai.Client(project=project_id, location=region)
    repository = AgentRepository(AsyncClient(project=project_id))
    fleet = AgentFleetService(repository)
    registrations = []
    for kind in AgentKind:
        module = __import__(f"agents.{kind.value}.agent", fromlist=["agent_app"])
        app = module.agent_app
        remote = client.agent_engines.create(
            agent=app,
            config={
                "display_name": f"FireKey {kind.value.title()} Agent {version}",
                "description": f"FireKey managed {kind.value} agent",
                "staging_bucket": staging_bucket,
                "requirements": str(_ROOT / "server" / "agents" / "requirements.txt"),
                "extra_packages": [
                    str(_ROOT / "server" / "agents"),
                    str(_ROOT / "server" / "core"),
                    str(_ROOT / "packages" / "contracts" / "src" / "contracts"),
                    str(_ROOT / "packages" / "policy" / "src" / "policy"),
                ],
                "env_vars": {"GOOGLE_CLOUD_PROJECT": project_id},
                "service_account": service_account,
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
            },
        )
        resource = remote.api_resource
        if resource is None or not resource.name:
            raise RuntimeError(f"Agent Runtime returned no resource for {kind.value}")
        registration = AgentRegistration(
            id=f"{kind.value}_{version.replace('.', '_')}",
            organisation_id=organisation_id,
            kind=kind,
            display_name=f"FireKey {kind.value.title()} Agent",
            version=version,
            skills=_SKILLS[kind],
            owner="FireKey Platform",
            identity=service_account,
            endpoint=f"https://{region}-aiplatform.googleapis.com",
            deployment=resource.name,
            region=region,
            approved_callers=frozenset({service_account}),
            tool_destinations=frozenset({"firestore", "firekey-browser", "firekey-broker"}),
            status=AgentStatus.READY,
            registered_at=datetime.now(UTC),
        )
        registrations.append(await fleet.register(registration))
    return tuple(registrations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--organisation", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--kms-key", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    import asyncio

    values = asyncio.run(
        deploy(
            args.project,
            args.organisation,
            args.region,
            args.staging_bucket,
            args.service_account,
            args.kms_key,
            args.version,
        )
    )
    print(json.dumps([value.model_dump(mode="json") for value in values]))


if __name__ == "__main__":
    main()
