import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from broker.evidence import GcsEvidenceSink
from connectors.google import GoogleRestClient
from contracts import AgentKind, AgentRegistration, AgentResult, AgentTask, PlaybookDraft
from google.cloud.firestore_v1 import AsyncClient
from opentelemetry import trace
from starlette.applications import Starlette
from telemetry import instrument, operation

from agents.armor import ModelArmorGuard
from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.runtime import AgentRuntimeService
from agents.storage import AgentRepository

_FLOW = (
    (AgentKind.PLAYBOOK, "build_playbook", "playbook"),
    (AgentKind.INVENTORY, "detect_stale_mapping", "preflight"),
    (AgentKind.PLANNER, "plan_rotation", "plan"),
    (AgentKind.OPERATOR, "execute_console_playbook", "create"),
)


async def run_fleet_probe(
    runtime: AgentRuntimeService,
    registrations: Mapping[AgentKind, AgentRegistration],
    evidence: GcsEvidenceSink,
    organisation_id: str,
    run_id: str,
    clock: Callable[[], datetime],
    *,
    require_traces: bool,
) -> tuple[dict[str, Any], bool]:
    scenario = _scenario(organisation_id, run_id, clock())
    context_record = {
        "schema": "uumi.fleet-context.v1",
        "organisation_id": organisation_id,
        "run_id": run_id,
        "scenario": "resend-browser-rotation",
        "credential_id": scenario["credential"]["id"],
        "service_id": scenario["binding"]["service_id"],
        "steps": [
            {"agent": kind.value, "skill": skill, "stage": stage} for kind, skill, stage in _FLOW
        ],
    }
    context_evidence = await evidence.store(
        organisation_id,
        run_id,
        "fleet-context",
        json.dumps(context_record, separators=(",", ":"), sort_keys=True).encode(),
        "application/json",
        clock(),
    )

    results: list[tuple[AgentResult, str | None]] = []
    playbook: dict[str, Any] | None = None
    for kind, skill, stage in _FLOW:
        task = _task(
            kind,
            skill,
            stage,
            organisation_id,
            run_id,
            scenario,
            playbook,
            context_evidence.id,
            clock(),
        )
        with operation("agent.fleet.step", {"agent": kind.value, "skill": skill, "stage": stage}):
            trace_id = _trace_id()
            result = await runtime.execute(task)
        results.append((result, trace_id))
        if kind is AgentKind.PLAYBOOK and result.succeeded:
            try:
                playbook = PlaybookDraft.model_validate(result.output).model_dump(mode="json")
            except ValueError:
                playbook = None

    passed = _passed(results, registrations, context_evidence.id, playbook, require_traces)
    report = {
        "schema": "uumi.fleet-probe.v1",
        "organisation_id": organisation_id,
        "run_id": run_id,
        "passed": passed,
        "recorded_at": clock().isoformat(),
        "context_evidence_id": context_evidence.id,
        "agents": [
            _agent_summary(registrations[kind], result, trace_id, stage)
            for (kind, _, stage), (result, trace_id) in zip(_FLOW, results, strict=True)
        ],
    }
    summary = await evidence.store(
        organisation_id,
        run_id,
        "fleet-probe",
        json.dumps(report, separators=(",", ":"), sort_keys=True).encode(),
        "application/json",
        clock(),
    )
    return {**report, "summary_evidence_id": summary.id}, passed


def _task(
    kind: AgentKind,
    skill: str,
    stage: str,
    organisation_id: str,
    run_id: str,
    scenario: dict[str, dict[str, Any]],
    playbook: dict[str, Any] | None,
    context_evidence_id: str,
    requested_at: datetime,
) -> AgentTask:
    context: dict[str, Any]
    if kind is AgentKind.PLAYBOOK:
        context = {
            "playbook_id": "playbook_resend_evidence",
            "walkthroughs": (
                {
                    "source_id": "walkthrough_resend_api_keys_v1",
                    "procedure": _playbook_draft(),
                },
            ),
        }
        objective = (
            "Sanitised Resend API key UI evidence: walkthrough_resend_api_keys_v1."
        )
    elif kind is AgentKind.INVENTORY:
        context = {
            "inventory_item": scenario["credential"],
            "bindings": (scenario["binding"],),
            "services": (),
        }
        objective = (
            "Inventory alignment for credential_fleet_evidence and its declared "
            "service_fleet_evidence consumer."
        )
    elif kind is AgentKind.PLANNER:
        context = {
            "run": scenario["run"],
            "inventory_item": scenario["credential"],
            "bindings": (scenario["binding"],),
            "services": (),
            "provider_connection": scenario["connection"],
            "controls": scenario["controls"],
            "published_playbook": (
                _published_playbook(playbook, organisation_id, requested_at)
                if playbook is not None
                else None
            ),
            "current_generation": scenario["generation"],
        }
        objective = (
            "Governed rotation plan for credential_fleet_evidence: all twelve ordered lifecycle "
            "stages and the configured rollback recovery action."
        )
    else:
        step_id = _creation_step_id(playbook)
        context = {
            "run": scenario["run"],
            "inventory_item": scenario["credential"],
            "provider_connection": scenario["connection"],
            "controls": scenario["controls"],
            "published_playbook": (
                _published_playbook(playbook, organisation_id, requested_at)
                if playbook is not None
                else None
            ),
            "step_id": step_id,
            "stage": stage,
        }
        objective = f"Readiness assessment for immutable {step_id} and the isolated browser worker."
    return AgentTask(
        id=f"task_{run_id.removeprefix('run_')}_{kind.value}",
        organisation_id=organisation_id,
        run_id=run_id,
        agent=kind,
        skill=skill,
        objective=objective,
        context=context,
        evidence_ids=(context_evidence_id,),
        requested_at=requested_at,
    )


def _creation_step_id(playbook: dict[str, Any] | None) -> str:
    if isinstance(playbook, dict):
        steps = playbook.get("steps")
        if isinstance(steps, (list, tuple)):
            matches = [
                step.get("id")
                for step in steps
                if isinstance(step, dict) and step.get("effect") == "create-credential"
            ]
            if len(matches) == 1 and isinstance(matches[0], str) and matches[0]:
                return matches[0]
    return "unavailable_create_step"


def _passed(
    results: list[tuple[AgentResult, str | None]],
    registrations: Mapping[AgentKind, AgentRegistration],
    context_evidence_id: str,
    playbook: dict[str, Any] | None,
    require_traces: bool,
) -> bool:
    expected = tuple(kind for kind, _, _ in _FLOW)
    identities = {registrations[kind].identity for kind in expected}
    deployments = {registrations[kind].deployment for kind in expected}
    trace_ids = [trace_id for _, trace_id in results]
    outputs = {result.agent: result.output for result, _ in results}
    return (
        tuple(registrations) == expected
        and len(identities) == len(expected)
        and len(deployments) == len(expected)
        and len(results) == len(expected)
        and tuple(result.agent for result, _ in results) == expected
        and all(result.succeeded for result, _ in results)
        and all(context_evidence_id in result.evidence_ids for result, _ in results)
        and all(len(result.evidence_ids) >= 3 for result, _ in results)
        and (not require_traces or all(trace_id is not None for trace_id in trace_ids))
        and playbook is not None
        and outputs.get(AgentKind.INVENTORY, {}).get("missing_inventory") == []
        and outputs.get(AgentKind.PLANNER, {}).get("decision") == "plan"
        and outputs.get(AgentKind.OPERATOR, {}).get("ready") is True
        and outputs.get(AgentKind.OPERATOR, {}).get("drift_detected") is False
    )


def _agent_summary(
    registration: AgentRegistration,
    result: AgentResult,
    trace_id: str | None,
    stage: str,
) -> dict[str, Any]:
    output = json.dumps(result.output, separators=(",", ":"), sort_keys=True).encode()
    return {
        "agent_id": registration.id,
        "kind": registration.kind.value,
        "version": registration.version,
        "identity": registration.identity,
        "deployment": registration.deployment,
        "registry": registration.registry,
        "ingress_gateway": registration.ingress_gateway,
        "egress_gateway": registration.egress_gateway,
        "stage": stage,
        "task_id": result.task_id,
        "skill": result.skill,
        "succeeded": result.succeeded,
        "trace_id": trace_id,
        "evidence_ids": list(result.evidence_ids),
        "output_keys": sorted(result.output),
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "error": result.error,
    }


def _trace_id() -> str | None:
    value = trace.get_current_span().get_span_context().trace_id
    return f"{value:032x}" if value else None


def _scenario(organisation_id: str, run_id: str, now: datetime) -> dict[str, dict[str, Any]]:
    timestamp = now.isoformat()
    credential = {
        "id": "credential_fleet_evidence",
        "organisation_id": organisation_id,
        "connection_id": "connection_resend_console",
        "secret_store_connection_id": "connection_secret_manager",
        "secret_resource": "projects/useuumi/secrets/uumi-resend-api-key",
        "secret_reference": "projects/useuumi/secrets/uumi-resend-api-key/versions/1",
        "provider": "resend",
        "kind": "api-key",
        "display_name": "Resend evidence credential",
        "provider_id": "key_fleet_previous",
        "scopes": ["sending_access"],
        "consumer_ids": ["service_fleet_evidence"],
        "active_generation_id": "generation_fleet_current",
        "control_version": "control_fleet_v1",
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 1,
    }
    connection = {
        "id": "connection_resend_console",
        "organisation_id": organisation_id,
        "platform": "resend",
        "display_name": "Resend",
        "roles": ["provider"],
        "interface": "browser",
        "authorization": "browser-session",
        "authorization_reference": "browser-session://resend/evidence",
        "capabilities": ["credential-create", "credential-revoke"],
        "allowed_resources": ["resend-api-keys"],
        "playbook_id": "playbook_resend_evidence",
        "playbook_version_id": "playbook_version_resend_evidence",
        "status": "ready",
        "authenticated_at": timestamp,
        "last_validated_at": timestamp,
        "region": "us-east1",
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 1,
    }
    binding = {
        "id": "binding_fleet_evidence",
        "organisation_id": organisation_id,
        "credential_id": credential["id"],
        "service_id": "service_fleet_evidence",
        "environment_id": "environment_fleet_evidence",
        "runtime_connection_id": "connection_cloud_run",
        "runtime_resource": "projects/useuumi/locations/us-east1/services/uumi-api",
        "runtime_secret_name": "RESEND_API_KEY",
        "secret_reference": credential["secret_reference"],
        "current_generation_id": credential["active_generation_id"],
        "required": True,
        "revision": 1,
    }
    run = {
        "id": run_id,
        "organisation_id": organisation_id,
        "credential_id": credential["id"],
        "trigger": {
            "source": "fleet-evidence",
            "kind": "rotation-requested",
            "event_id": f"event_{run_id.removeprefix('run_')}",
            "actor_id": "uumi_fleet_probe",
            "reason": "Prove the governed four-agent rotation path.",
            "urgency": "scheduled",
            "received_at": timestamp,
        },
        "control_version": credential["control_version"],
        "stage": "plan",
        "status": "running",
        "fencing_token": 1,
        "browser_playbook_version": connection["playbook_version_id"],
        "current_generation_id": credential["active_generation_id"],
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 2,
    }
    controls = {
        "id": credential["control_version"],
        "organisation_id": organisation_id,
        "credential_id": credential["id"],
        "number": 1,
        "definition": {
            "allowed_tools": [
                "browser.secure-capture",
                "secret.storeVersion",
                "runtime.deployCandidate",
                "verification.run",
                "runtime.shiftTraffic",
                "browser.revokeCredential",
            ],
            "protected_tools": ["browser.revokeCredential"],
            "maximum_observation_seconds": 300,
            "require_revoke_approval": True,
            "preserve_old_generation": True,
            "require_generation_telemetry": True,
            "recovery": {"create": {"mode": "cleanup", "actions": ["discard-candidate"]}},
        },
        "digest": "0" * 64,
        "created_by": "uumi_fleet_probe",
        "created_at": timestamp,
    }
    generation = {
        "id": credential["active_generation_id"],
        "organisation_id": organisation_id,
        "credential_id": credential["id"],
        "secret_reference": credential["secret_reference"],
        "provider_id": credential["provider_id"],
        "state": "active",
        "created_at": timestamp,
    }
    return {
        "credential": credential,
        "connection": connection,
        "binding": binding,
        "run": run,
        "controls": controls,
        "generation": generation,
    }


def _playbook_draft() -> dict[str, Any]:
    return {
        "name": "Resend API key rotation",
        "platform": "resend",
        "allowed_domains": ["resend.com"],
        "login_url_pattern": "https://resend.com/login*",
        "steps": [
            {
                "id": "create_resend_key",
                "stage": "create",
                "tool": "browser.secure-capture",
                "operation": "click",
                "objective": "Create a restricted Resend API key",
                "parameters": {},
                "protected": False,
                "evidence_checks": ["credential-created", "secure-capture-complete"],
                "effect": "create-credential",
                "selectors": [
                    {"kind": "role", "value": "button", "name": "Create API key", "exact": True}
                ],
                "checkpoint": {
                    "url_pattern": "https://resend.com/api-keys*",
                    "required_text": ["API Keys"],
                    "forbidden_text": [],
                },
                "secure_field": {
                    "name": "api_key",
                    "selector": {"kind": "css", "value": "input[readonly]", "exact": True},
                    "provider_id_selector": {
                        "kind": "css",
                        "value": "[data-key-id]",
                        "exact": True,
                    },
                },
                "outputs": [],
                "timeout_seconds": 30,
                "retry_limit": 0,
            },
            {
                "id": "revoke_old_key",
                "stage": "revoke",
                "tool": "browser.revokeCredential",
                "operation": "click",
                "objective": "Revoke the superseded Resend API key",
                "parameters": {},
                "protected": False,
                "evidence_checks": ["provider-revoked"],
                "effect": "revoke-credential",
                "selectors": [{"kind": "role", "value": "button", "name": "Revoke", "exact": True}],
                "checkpoint": {
                    "url_pattern": "https://resend.com/api-keys*",
                    "required_text": ["API Keys"],
                    "forbidden_text": [],
                },
                "secure_field": None,
                "outputs": [],
                "timeout_seconds": 30,
                "retry_limit": 0,
            },
        ],
    }


def _published_playbook(
    definition: dict[str, Any], organisation_id: str, recorded_at: datetime
) -> dict[str, Any]:
    return {
        "id": "playbook_version_resend_evidence",
        "organisation_id": organisation_id,
        "playbook_id": "playbook_resend_evidence",
        "number": 1,
        "definition": definition,
        "digest": hashlib.sha256(
            json.dumps(definition, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "state": "published",
        "source_ids": [],
        "published_by": "uumi_fleet_probe",
        "published_at": recorded_at.isoformat(),
        "created_by": "uumi_fleet_probe",
        "created_at": recorded_at.isoformat(),
    }


async def _main() -> int:
    project_id = _required("UUMI_PROJECT_ID")
    database = os.environ.get("UUMI_FIRESTORE_DATABASE", "(default)")
    region = _required("UUMI_REGION")
    bucket = _required("UUMI_EVIDENCE_BUCKET")
    template = _required("UUMI_MODEL_ARMOR_TEMPLATE")
    response_template = os.environ.get("UUMI_MODEL_ARMOR_RESPONSE_TEMPLATE", template)
    organisation_id = _required("UUMI_PROBE_ORGANISATION")
    run_id = _required("UUMI_PROBE_RUN_ID")
    instrument(Starlette(), "uumi-fleet-probe")
    firestore = AsyncClient(project=project_id, database=database)
    google = GoogleRestClient(timeout=180)
    repository = AgentRepository(firestore)
    fleet = AgentFleetService(repository)
    evidence = GcsEvidenceSink(google, firestore, bucket, region)
    continuity = AgentContinuityService(repository, google, project_id, database, _now)
    runtime = AgentRuntimeService(
        fleet,
        continuity,
        google,
        ModelArmorGuard(google, template, evidence, _now, response_template),
        _now,
    )
    try:
        registrations = {
            kind: await fleet.resolve(organisation_id, kind, skill) for kind, skill, _ in _FLOW
        }
        report, passed = await run_fleet_probe(
            runtime,
            registrations,
            evidence,
            organisation_id,
            run_id,
            _now,
            require_traces=True,
        )
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
        return 0 if passed else 1
    finally:
        await google.close()
        firestore.close()  # type: ignore[no-untyped-call]
        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush()


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
