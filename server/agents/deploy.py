import argparse
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
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
_MANAGED_AGENT_IDENTITY = re.compile(
    r"^agents\.global\.(?:org|project)-\d+\.system\.id\.goog/"
    r"resources/aiplatform/projects/\d+/locations/[a-z0-9-]+/reasoningEngines/\d+$"
)
_AGENT_SOURCE_PACKAGES = (
    (_ROOT / "server" / "agents", "agents"),
    (_ROOT / "server" / "browser", "browser"),
    (_ROOT / "server" / "connectors", "connectors"),
    (_ROOT / "server" / "core", "core"),
    (_ROOT / "packages" / "contracts" / "src" / "contracts", "contracts"),
    (_ROOT / "packages" / "policy" / "src" / "policy", "policy"),
    (_ROOT / "packages" / "telemetry" / "src" / "telemetry", "telemetry"),
)


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
    catalog_credentials: Credentials | None = None,
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
            catalog_credentials,
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
    catalog_credentials: Credentials | None = None,
) -> tuple[AgentRegistration, ...]:
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = region
    client = vertexai.Client(
        project=project_id,
        location=region,
        http_options={"api_version": "v1beta1"},
        credentials=credentials,
    )
    # Keep the VPC-SC operator identity on catalog calls; runtime calls use impersonation.
    repository = AgentRepository(
        AsyncClient(project=project_id, credentials=catalog_credentials or credentials)
    )
    fleet = AgentFleetService(repository)
    registrations = []
    for kind in AgentKind:
        registration_id = f"{kind.value}_{version.replace('.', '_')}"
        try:
            current = await repository.get(organisation_id, registration_id)
        except ResourceNotFoundError:
            pass
        else:
            if not _matches_existing_registration(
                current,
                kind,
                version,
                region,
                ingress_gateway,
                egress_gateway,
                approved_callers,
            ):
                raise RuntimeError(
                    f"existing {kind.value} registration does not match this deployment"
                )
            await _grant_callers(
                google,
                project_id,
                region,
                current.deployment,
                caller_role,
                approved_callers,
            )
            registrations.append(await fleet.register(current))
            continue
        module = __import__(f"agents.{kind.value}.agent", fromlist=["agent_app"])
        app = module.agent_app
        remote = _matching_deployment(
            client,
            kind,
            version,
            kms_key,
            ingress_gateway,
            egress_gateway,
        )
        if remote is None:
            with _staged_agent_source() as source_packages:
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
                        source_packages,
                    ),
                )
        resource = remote.api_resource
        if resource is None or not resource.name:
            raise RuntimeError(f"Agent Runtime returned no resource for {kind.value}")
        deployment = _canonical_deployment(resource.name, region)
        deployment_project = deployment.split("/")[1]
        identity = _effective_identity(resource)
        await _grant_callers(
            google,
            project_id,
            region,
            deployment,
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
            deployment=deployment,
            registry=(
                f"//agentregistry.googleapis.com/projects/{deployment_project}/locations/{region}"
            ),
            ingress_gateway=_canonical_gateway(ingress_gateway, deployment_project, region),
            egress_gateway=_canonical_gateway(egress_gateway, deployment_project, region),
            region=region,
            approved_callers=approved_callers,
            tool_destinations=frozenset({"firestore"}),
            status=AgentStatus.READY,
            registered_at=datetime.now(UTC),
        )
        registrations.append(await fleet.register(registration))
    return tuple(registrations)


def _matches_existing_registration(
    registration: AgentRegistration,
    kind: AgentKind,
    version: str,
    region: str,
    ingress_gateway: str,
    egress_gateway: str,
    approved_callers: frozenset[str],
) -> bool:
    try:
        deployment_project = registration.deployment.split("/")[1]
        expected_ingress = _canonical_gateway(ingress_gateway, deployment_project, region)
        expected_egress = _canonical_gateway(egress_gateway, deployment_project, region)
    except (IndexError, RuntimeError):
        return False
    return (
        registration.kind is kind
        and registration.version == version
        and registration.region == region
        and registration.ingress_gateway == expected_ingress
        and registration.egress_gateway == expected_egress
        and registration.approved_callers == approved_callers
        and registration.status is AgentStatus.READY
    )


def _deployment_config(
    project_id: str,
    kind: AgentKind,
    version: str,
    staging_bucket: str,
    kms_key: str,
    ingress_gateway: str,
    egress_gateway: str,
    source_packages: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "display_name": f"Uumi {kind.value.title()} Agent {version}",
        "description": f"Uumi managed {kind.value} agent",
        "staging_bucket": staging_bucket,
        "requirements": str(_ROOT / "server" / "agents" / "requirements.txt"),
        "extra_packages": list(source_packages or (name for _, name in _AGENT_SOURCE_PACKAGES)),
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


@contextmanager
def _staged_agent_source() -> Iterator[tuple[str, ...]]:
    previous = Path.cwd()
    with TemporaryDirectory(prefix="uumi-agent-source-") as directory:
        root = Path(directory)
        for source, name in _AGENT_SOURCE_PACKAGES:
            copytree(
                source,
                root / name,
                ignore=ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        os.chdir(root)
        try:
            yield tuple(name for _, name in _AGENT_SOURCE_PACKAGES)
        finally:
            os.chdir(previous)


def _effective_identity(resource: Any) -> str:
    spec = getattr(resource, "spec", None)
    identity = getattr(spec, "effective_identity", None)
    if not isinstance(identity, str):
        raise RuntimeError("Agent Runtime returned no managed Agent Identity")
    value = identity.removeprefix("principal://")
    if _MANAGED_AGENT_IDENTITY.fullmatch(value) is None:
        raise RuntimeError("Agent Runtime returned no managed Agent Identity")
    return f"principal://{value}"


def _canonical_deployment(name: str, region: str) -> str:
    if (
        re.fullmatch(
            rf"projects/\d+/locations/{re.escape(region)}/reasoningEngines/\d+",
            name,
        )
        is None
    ):
        raise RuntimeError("Agent Runtime returned an invalid regional resource name")
    return name


def _canonical_gateway(name: str, project: str, region: str) -> str:
    match = re.fullmatch(
        rf"projects/[^/]+/locations/{re.escape(region)}/agentGateways/(?P<gateway>[^/]+)",
        name,
    )
    if match is None or not project.isdigit():
        raise RuntimeError("Agent Runtime gateway cannot be paired with its deployment")
    return f"projects/{project}/locations/{region}/agentGateways/{match.group('gateway')}"


def _matching_deployment(
    client: Any,
    kind: AgentKind,
    version: str,
    kms_key: str,
    ingress_gateway: str,
    egress_gateway: str,
) -> Any | None:
    matches = []
    for remote in client.agent_engines.list():
        resource = remote.api_resource
        spec = getattr(resource, "spec", None)
        deployment = getattr(spec, "deployment_spec", None)
        gateways = getattr(deployment, "agent_gateway_config", None)
        ingress = getattr(gateways, "client_to_agent_config", None)
        egress = getattr(gateways, "agent_to_anywhere_config", None)
        encryption = getattr(resource, "encryption_spec", None)
        labels = getattr(resource, "labels", None)
        if (
            getattr(resource, "display_name", None) == f"Uumi {kind.value.title()} Agent {version}"
            and isinstance(labels, dict)
            and labels.get("uumi-agent") == kind.value
            and labels.get("uumi-version") == version.replace(".", "-")
            and getattr(encryption, "kms_key_name", None) == kms_key
            and getattr(ingress, "agent_gateway", None) == ingress_gateway
            and getattr(egress, "agent_gateway", None) == egress_gateway
        ):
            matches.append(remote)
    if len(matches) > 1:
        raise RuntimeError(f"multiple matching {kind.value} Agent Runtime deployments exist")
    return matches[0] if matches else None


async def _grant_callers(
    google: GoogleRestClient,
    project_id: str,
    region: str,
    deployment: str,
    role: str,
    callers: frozenset[str],
) -> None:
    if role != f"projects/{project_id}/roles/uumiAgentCaller":
        raise ValueError("caller role must be the Uumi least-privilege project role")
    if not re.fullmatch(
        rf"projects/[^/]+/locations/{re.escape(region)}/reasoningEngines/\d+",
        deployment,
    ):
        raise ValueError("deployment must be a regional Agent Runtime resource")
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


def _deployment_credentials(
    service_account: str | None,
    source_credentials: Credentials | None = None,
) -> Credentials | None:
    if service_account is None:
        return None
    source = source_credentials
    if source is None:
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

    source_credentials = None
    if args.impersonate_service_account is not None:
        source_credentials, _ = google.auth.default(scopes=(_CLOUD_PLATFORM_SCOPE,))
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
            _deployment_credentials(args.impersonate_service_account, source_credentials),
            source_credentials,
        )
    )
    print(json.dumps([value.model_dump(mode="json") for value in values]))


if __name__ == "__main__":
    main()
