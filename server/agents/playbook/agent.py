from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared import analyse_walkthrough, build_playbook, generate_dry_run
from agents.shared.app import managed_app

root_agent = Agent(
    name="playbook_builder_agent",
    model="gemini-3.5-flash",
    description="Builds typed provider playbooks from sanitised walkthrough evidence.",
    instruction="""Analyse only sanitised walkthrough evidence. Use semantic selectors and exact
page checkpoints. Include complete lifecycle, evidence checks, recovery, protected creation and
revocation, and explicit secure capture for computer-use. Validate every candidate with the
build_playbook tool. Never place secrets in a playbook or response.""",
    tools=[analyse_walkthrough, build_playbook, generate_dry_run],
    mode="task",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="firekey_playbook", root_agent=root_agent)
agent_app = managed_app(app)
