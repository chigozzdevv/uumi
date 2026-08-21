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
    """Return controls, inventory, and the optional browser procedure for a run."""
    context = AgentContext(tool_context)
    run = await context.run()
    credential = await context.document(
        FirestorePaths.credential(context.organisation_id, _string(run, "credential_id"))
    )
    connection = await context.document(
        FirestorePaths.connection(context.organisation_id, _string(credential, "connection_id"))
    )
    controls = await context.document(
        FirestorePaths.control_version(
            context.organisation_id,
            _string(run, "credential_id"),
            _string(run, "control_version"),
        )
    )
    version = None
    playbook_id = connection.get("playbook_id")
    version_id = connection.get("playbook_version_id")
    if isinstance(playbook_id, str) and isinstance(version_id, str):
        version = await context.document(
            FirestorePaths.playbook_version(
                context.organisation_id,
                playbook_id,
                version_id,
            )
        )
    return {
        "credential": credential,
        "provider_connection": connection,
        "controls": controls,
        "browser_playbook": version,
    }


async def bind_playbook(tool_context: ToolContext) -> dict[str, Any]:
    """Confirm that a browser connection still references its published playbook."""
    selected = await select_strategy(tool_context)
    connection = selected["provider_connection"]
    version = selected["browser_playbook"]
    if connection.get("interface") == "browser" and (
        not isinstance(version, dict) or version.get("state") != "published"
    ):
        raise ValueError("browser connection is not bound to a published playbook")
    return selected


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
    """Canonicalise one versioned browser procedure from sanitised source evidence."""
    AgentContext(tool_context)
    draft = PlaybookDraft.model_validate(definition)
    validate_definition(draft)
    return draft.model_dump(mode="json")


async def validate_playbook(
    definition: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any]:
    """Validate browser-only actions, checkpoints, domains, and secure capture declarations."""
    AgentContext(tool_context)
    draft = PlaybookDraft.model_validate(definition)
    validate_definition(draft)
    return {
        "valid": True,
        "actions": len(draft.steps),
        "create_actions": sum(step.stage.value == "create" for step in draft.steps),
        "revoke_actions": sum(step.stage.value == "revoke" for step in draft.steps),
        "secure_capture_declared": any(step.secure_field is not None for step in draft.steps),
    }


async def analyse_walkthrough(
    playbook_id: str, source_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Load one sanitised video, recording, text, or linked-resource evidence record."""
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


async def execute_console_playbook(step_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Load exactly one immutable browser step for the separate Computer Use worker."""
    context = AgentContext(tool_context)
    selected = await bind_playbook(tool_context)
    version = selected.get("browser_playbook")
    if not isinstance(version, dict):
        raise ValueError("run does not use a browser playbook")
    steps = version.get("definition", {}).get("steps", [])
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
            from agents.redact import redact

            values.append(redact(data))
    return values


def _string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise ValueError(f"agent context is missing {name}")
    return result
