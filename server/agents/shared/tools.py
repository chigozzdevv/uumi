from typing import Any

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
    """Return the bound run, inventory, and current generation."""
    context = AgentContext(tool_context)
    run = _object(context, "run")
    credential_id = _string(run, "credential_id")
    consumers = await resolve_consumers(credential_id, tool_context)
    generation = context.object("current_generation", required=False)
    return {"run": run, **consumers, "current_generation": generation}


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
