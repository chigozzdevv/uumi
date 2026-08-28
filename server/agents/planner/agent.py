from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared.app import managed_app
from agents.shared.model import managed_model
from agents.shared.models import PlannerOutput
from agents.shared.tools import (
    bind_playbook,
    diagnose_failed_stage,
    plan_rotation,
    recommend_authorised_recovery,
    select_strategy,
)

root_agent = Agent(
    name="rotation_planner_agent",
    model=managed_model(),
    description="Builds rotation plans from inventory and pinned credential controls.",
    instruction="""For plan_rotation, use the authoritative run, confirmed inventory, active
credential controls, and browser playbook only when the provider connection uses a browser.
Call both plan_rotation and select_strategy. For decision=plan, copy ordered_stages and
recovery_actions exactly from select_strategy, choose an observation_seconds value no greater
than the controls maximum, and return the selected strategy and rationale. For
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
    mode="chat",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="uumi_planner", root_agent=root_agent)
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
