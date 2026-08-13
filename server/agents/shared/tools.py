from typing import Any

from contracts import PlaybookDraft
from core.errors import ResourceNotFoundError
from core.playbook import validate_definition
from core.storage.paths import FirestorePaths
from google.adk.agents.context import Context as ToolContext

from agents.shared.context import AgentContext


async def correlate_exposure(incident_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Load one incident and its credential inventory for exposure correlation."""
    context = AgentContext(tool_context)
    incident = await context.document(FirestorePaths.incident(context.organisation_id, incident_id))
    credential_id = incident.get("credential_id")
    credential = None
    if isinstance(credential_id, str):
        credential = await context.document(
            FirestorePaths.credential(context.organisation_id, credential_id)
        )
    return {"incident": incident, "credential": credential}


async def resolve_consumers(credential_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Return the credential and its declared consumer bindings."""
    context = AgentContext(tool_context)
    credential = await context.document(
        FirestorePaths.credential(context.organisation_id, credential_id)
    )
    bindings = await _collection(context, "bindings", "credential_id", credential_id)
    services = []
    for binding in bindings:
        service_id = binding.get("service_id")
        if isinstance(service_id, str):
            services.append(
                await context.document(FirestorePaths.service(context.organisation_id, service_id))
            )
    return {"credential": credential, "bindings": bindings, "services": services}


async def detect_stale_mapping(credential_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Compare inventory consumers with observed run context and flag stale mappings."""
    context = AgentContext(tool_context)
    consumers = await resolve_consumers(credential_id, tool_context)
    run = await context.run()
    declared = {item.get("id") for item in consumers["services"]}
    observed = set(run.get("consumer_ids", []))
    return {
        "declared_consumers": sorted(item for item in declared if isinstance(item, str)),
        "observed_consumers": sorted(item for item in observed if isinstance(item, str)),
        "missing_inventory": sorted(observed.difference(declared)),
        "unobserved_inventory": sorted(declared.difference(observed)),
    }


async def plan_rotation(tool_context: ToolContext) -> dict[str, Any]:
    """Load the authoritative run, credential, consumer bindings, and current generation."""
    context = AgentContext(tool_context)
    run = await context.run()
    credential_id = _string(run, "credential_id")
    consumers = await resolve_consumers(credential_id, tool_context)
    generation_id = run.get("old_generation_id")
    generation = None
    if isinstance(generation_id, str):
        generation = await context.document(
            FirestorePaths.generation(context.organisation_id, generation_id)
        )
    return {"run": run, **consumers, "current_generation": generation}


async def select_strategy(tool_context: ToolContext) -> dict[str, Any]:
    """Return allowed execution choices from the assigned immutable playbook."""
    context = AgentContext(tool_context)
    run = await context.run()
    assignment = await context.document(
        FirestorePaths.assignment(context.organisation_id, _string(run, "credential_id"))
    )
    version = await context.document(
        FirestorePaths.playbook_version(
            context.organisation_id,
            _string(assignment, "playbook_id"),
            _string(assignment, "version_id"),
        )
    )
    return {"assignment": assignment, "version": version}


async def bind_playbook(tool_context: ToolContext) -> dict[str, Any]:
    """Confirm that the assigned playbook and connections are still active and exact."""
    context = AgentContext(tool_context)
    run = await context.run()
    selected = await select_strategy(tool_context)
    assignment = selected["assignment"]
    version = selected["version"]
    dry_run = isinstance(run.get("dry_run_id"), str)
    if dry_run:
        if not assignment.get("dry_run_only") or version.get("state") != "test-required":
            raise ValueError("dry run is not bound to its test-required playbook")
    elif assignment.get("dry_run_only") or version.get("state") != "active":
        raise ValueError("production run is not bound to an active playbook")
    connections = []
    for connection_id in assignment.get("connection_ids", []):
        if isinstance(connection_id, str):
            connections.append(
                await context.document(
                    FirestorePaths.connection(context.organisation_id, connection_id)
                )
            )
    return {**selected, "connections": connections, "dry_run": dry_run}


async def diagnose_failed_stage(tool_context: ToolContext) -> dict[str, Any]:
    """Load the failed run state and the exact precomputed recovery branch."""
    context = AgentContext(tool_context)
    run = await context.run()
    plan_id = _string(run, "plan_id")
    plan = await context.document(FirestorePaths.plan(context.organisation_id, plan_id))
    stage = _string(run, "stage")
    recovery_id = plan.get("recovery_ids", {}).get(stage)
    if not isinstance(recovery_id, str):
        raise ValueError("failed stage has no planned recovery branch")
    recovery = await context.document(FirestorePaths.recovery(context.organisation_id, recovery_id))
    return {"run": run, "plan": plan, "recovery": recovery}


async def recommend_authorised_recovery(tool_context: ToolContext) -> dict[str, Any]:
    """Return only the recovery branch already bound into the immutable rotation plan."""
    context = await diagnose_failed_stage(tool_context)
    recovery = context["recovery"]
    return {
        "recovery_id": recovery.get("id"),
        "mode": recovery.get("mode"),
        "failed_stage": recovery.get("failed_stage"),
        "steps": recovery.get("steps", []),
    }


async def build_playbook(definition: dict[str, Any], tool_context: ToolContext) -> dict[str, Any]:
    """Validate a complete provider playbook against FireKey lifecycle invariants."""
    AgentContext(tool_context)
    draft = PlaybookDraft.model_validate(definition)
    validate_definition(draft)
    return draft.model_dump(mode="json")


async def analyse_walkthrough(
    playbook_id: str, source_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Load one sanitised walkthrough evidence record for playbook analysis."""
    context = AgentContext(tool_context)
    source = await context.document(
        FirestorePaths.walkthrough(context.organisation_id, playbook_id, source_id)
    )
    if source.get("status") != "ready" or not isinstance(source.get("analysis"), dict):
        raise ValueError("walkthrough analysis is not ready")
    analysis = source["analysis"]
    if not isinstance(analysis, dict):
        raise ValueError("walkthrough analysis is invalid")
    return analysis


async def generate_dry_run(
    playbook_id: str, version_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Return the immutable playbook and isolated environment inputs for a real dry run."""
    context = AgentContext(tool_context)
    version = await context.document(
        FirestorePaths.playbook_version(context.organisation_id, playbook_id, version_id)
    )
    draft = PlaybookDraft.model_validate(version.get("definition"))
    validate_definition(draft)
    return {
        "playbook_id": playbook_id,
        "version_id": version_id,
        "digest": version.get("digest"),
        "definition": draft.model_dump(mode="json"),
        "required_checks": sorted(
            {check for step in draft.steps for check in step.evidence_checks}
        ),
    }


async def execute_console_playbook(step_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Load exactly one immutable browser step for the separate Computer Use worker."""
    context = AgentContext(tool_context)
    selected = await bind_playbook(tool_context)
    steps = selected["version"].get("definition", {}).get("steps", [])
    matches = [step for step in steps if step.get("id") == step_id]
    if len(matches) != 1 or not str(matches[0].get("tool", "")).startswith("browser."):
        raise ValueError("requested step is not one immutable browser action")
    return {"step": matches[0], "run": await context.run()}


async def detect_interface_drift(step_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Compare an immutable browser step with the latest sanitised browser checkpoint."""
    context = AgentContext(tool_context)
    execution = await execute_console_playbook(step_id, tool_context)
    browser_id = f"browser_{context.run_id.removeprefix('run_')}"
    try:
        session = await context.document(
            FirestorePaths.browser(context.organisation_id, browser_id)
        )
    except ResourceNotFoundError:
        session = None
    return {"expected_step": execution["step"], "browser_session": session}


async def _collection(
    context: AgentContext, name: str, field: str, value: str
) -> list[dict[str, Any]]:
    root = f"{FirestorePaths.organisation(context.organisation_id)}/{name}"
    values = []
    async for snapshot in context.client.collection(root).where(field, "==", value).stream():
        data = snapshot.to_dict()
        if data is not None:
            from agents.shared.context import redact

            values.append(redact(data))
    return values


def _string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise ValueError(f"agent context is missing {name}")
    return result
