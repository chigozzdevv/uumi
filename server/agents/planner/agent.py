from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared import (
    bind_playbook,
    diagnose_failed_stage,
    plan_rotation,
    recommend_authorised_recovery,
    select_strategy,
)
from agents.shared.app import managed_app
from agents.shared.models import PlannerOutput

root_agent = Agent(
    name="rotation_planner_agent",
    model="gemini-3.5-flash",
    description="Builds rotation plans from inventory and immutable playbooks.",
    instruction="""For plan_rotation, use the authoritative run and assigned immutable playbook
and return decision=plan with every lifecycle stage and its recovery. For
recommend_authorised_recovery, load the exact bound recovery, return decision=recovery only when
it remains eligible, and copy its ID and mode exactly; otherwise return decision=escalate. Never
add tools, change actions, or mutate resources. Return structured JSON.""",
    tools=[
        plan_rotation,
        select_strategy,
        bind_playbook,
        diagnose_failed_stage,
        recommend_authorised_recovery,
    ],
    output_schema=PlannerOutput,
    output_key="rotation_plan",
    mode="task",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="firekey_planner", root_agent=root_agent)
agent_app = managed_app(
    app,
    {
        "plan_rotation",
        "select_strategy",
        "bind_playbook",
        "diagnose_failed_stage",
        "recommend_authorised_recovery",
    },
)
