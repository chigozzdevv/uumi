from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared import bind_playbook, plan_rotation, select_strategy
from agents.shared.app import managed_app

root_agent = Agent(
    name="rotation_planner_agent",
    model="gemini-3.5-flash",
    description="Builds rotation plans from inventory and immutable playbooks.",
    instruction="""Use the authoritative run and assigned immutable playbook. Produce an ordered
plan covering create, store, deploy, deterministic verify, rollout, observe, approval, revoke,
and recovery. Do not add unregistered tools or mutate resources. Return structured JSON.""",
    tools=[plan_rotation, select_strategy, bind_playbook],
    mode="task",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="firekey_planner", root_agent=root_agent)
agent_app = managed_app(app)
