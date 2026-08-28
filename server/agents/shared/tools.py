from typing import Any

from contracts import Stage
from google.adk.agents.context import Context as ToolContext

from agents.shared.context import AgentContext


async def correlate_exposure(incident_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Return the control-plane-bound incident and credential inventory."""
    context = AgentContext(tool_context)
    incident = context.object("incident")
    assert incident is not None
    if incident.get("id") != incident_id:
        raise ValueError("requested incident is not bound to the managed task")
    credential = context.object("inventory_item", required=False)
    return {"incident": incident, "credential": credential}


async def resolve_consumers(credential_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Return the control-plane-bound credential and consumer bindings."""
    context = AgentContext(tool_context)
    credential = context.object("inventory_item")
    assert credential is not None
    if credential.get("id") != credential_id:
        raise ValueError("requested credential is not bound to the managed task")
    bindings = list(context.objects("bindings"))
    if any(item.get("credential_id") != credential_id for item in bindings):
        raise ValueError("managed task contains a binding for another credential")
    services = list(context.objects("services"))
    return {"credential": credential, "bindings": bindings, "services": services}


async def detect_stale_mapping(credential_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Compare inventory consumers with observed run context and flag stale mappings."""
    consumers = await resolve_consumers(credential_id, tool_context)
    declared = {item.get("service_id") for item in consumers["bindings"]}
    observed = set(consumers["credential"].get("consumer_ids", []))
    return {
        "declared_consumers": sorted(item for item in declared if isinstance(item, str)),
        "observed_consumers": sorted(item for item in observed if isinstance(item, str)),
        "missing_inventory": sorted(observed.difference(declared)),
        "unobserved_inventory": sorted(declared.difference(observed)),
    }


async def plan_rotation(tool_context: ToolContext) -> dict[str, Any]:
    """Return one complete plan derived from the immutable task snapshot."""
    selected = await select_strategy(tool_context)
    credential = selected["credential"]
    connection = selected["provider_connection"]
    controls = selected["controls"]
    definition = controls.get("definition")
    if not isinstance(definition, dict):
        raise ValueError("managed task controls have no definition")
    maximum = definition.get("maximum_observation_seconds")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or maximum < 60
        or not float(maximum).is_integer()
    ):
        raise ValueError("managed task controls have no valid observation window")
    maximum_seconds = int(maximum)
    consumer_ids = credential.get("consumer_ids")
    if not isinstance(consumer_ids, list) or not consumer_ids:
        raise ValueError("managed task credential has no declared consumers")
    capabilities = connection.get("capabilities")
    capability_set = set(capabilities) if isinstance(capabilities, list) else set()
    if len(consumer_ids) > 1:
        strategy = "multi-consumer"
    elif "dual-slot" in capability_set:
        strategy = "dual-slot"
    elif definition.get("preserve_old_generation") is not False:
        strategy = "parallel"
    else:
        strategy = "immediate"
    recovery_actions = selected["recovery_actions"]
    if not recovery_actions:
        raise ValueError("managed task controls have no recovery actions")
    return {
        "decision": "plan",
        "strategy": strategy,
        "observation_seconds": min(maximum_seconds, 300),
        "ordered_stages": selected["ordered_stages"],
        "recovery_actions": recovery_actions,
        "recovery_id": None,
        "recovery_mode": None,
        "eligible": None,
        "rationale": "The selected strategy follows the bound consumer count and controls.",
    }


async def select_strategy(tool_context: ToolContext) -> dict[str, Any]:
    """Return control-plane-bound controls, inventory, and browser procedure."""
    context = AgentContext(tool_context)
    run = _object(context, "run")
    credential = _object(context, "inventory_item")
    if credential.get("id") != _string(run, "credential_id"):
        raise ValueError("managed task run and credential bindings differ")
    connection = _object(context, "provider_connection")
    if connection.get("id") != _string(credential, "connection_id"):
        raise ValueError("managed task provider binding changed")
    controls = _object(context, "controls")
    version = context.object("published_playbook", required=False)
    definition = controls.get("definition")
    recovery = definition.get("recovery") if isinstance(definition, dict) else None
    recovery_actions: list[str] = []
    if isinstance(recovery, dict):
        for stage in Stage:
            branch = recovery.get(stage.value)
            actions = branch.get("actions") if isinstance(branch, dict) else None
            if isinstance(actions, list):
                recovery_actions.extend(item for item in actions if isinstance(item, str))
    return {
        "credential": credential,
        "provider_connection": connection,
        "controls": controls,
        "browser_playbook": version,
        "ordered_stages": [stage.value for stage in Stage],
        "recovery_actions": list(dict.fromkeys(recovery_actions)),
    }


async def bind_playbook(tool_context: ToolContext) -> dict[str, Any]:
    """Confirm that a browser connection still references its published playbook."""
    selected = await select_strategy(tool_context)
    connection = selected["provider_connection"]
    version = selected["browser_playbook"]
    if connection.get("interface") == "browser" and not isinstance(version, dict):
        raise ValueError("browser connection is not bound to a published playbook")
    return selected


async def diagnose_failed_stage(tool_context: ToolContext) -> dict[str, Any]:
    """Return the bound failed run and its exact precomputed recovery branch."""
    context = AgentContext(tool_context)
    run = _object(context, "run")
    plan_id = _string(run, "plan_id")
    plan = _object(context, "plan")
    if plan.get("id") != plan_id:
        raise ValueError("managed task recovery plan binding changed")
    stage = _string(run, "stage")
    recovery_id = plan.get("recovery_ids", {}).get(stage)
    if not isinstance(recovery_id, str):
        raise ValueError("failed stage has no planned recovery branch")
    recovery = _object(context, "recovery")
    if recovery.get("id") != recovery_id:
        raise ValueError("managed task recovery binding changed")
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


async def analyse_walkthrough(source_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Load one control-plane-bound sanitised walkthrough analysis."""
    context = AgentContext(tool_context)
    if not isinstance(context.value("playbook_id"), str):
        raise ValueError("managed task is missing its playbook binding")
    matches = [
        item for item in context.objects("walkthroughs") if item.get("source_id") == source_id
    ]
    if len(matches) != 1:
        raise ValueError("walkthrough analysis is not bound to the managed task")
    return matches[0]


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
    return {"step": matches[0], "run": _object(context, "run")}


async def detect_interface_drift(step_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Compare an immutable browser step with the latest sanitised browser checkpoint."""
    context = AgentContext(tool_context)
    execution = await execute_console_playbook(step_id, tool_context)
    session = context.object("browser_checkpoint", required=False)
    return {"expected_step": execution["step"], "browser_session": session}


def _object(context: AgentContext, name: str) -> dict[str, Any]:
    value = context.object(name)
    assert value is not None
    return value


def _string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise ValueError(f"agent context is missing {name}")
    return result
