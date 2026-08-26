from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared.app import managed_app
from agents.shared.model import managed_model
from agents.shared.models import PlaybookAgentDraft
from agents.shared.tools import analyse_walkthrough, build_playbook, validate_playbook

root_agent = Agent(
    name="playbook_builder_agent",
    model=managed_model(),
    description="Builds versioned browser procedures from sanitised source evidence.",
    instruction="""Analyse only sanitised source evidence. Produce ordered browser actions for
credential creation and revocation with deterministic selectors, exact page checkpoints, and
explicit Secure Capture for generated values. Do not add triggers, approvals, runtime deployment,
verification, rollout, observation, or recovery; those belong to credential controls and
orchestration. Write each objective as the exact visible browser action, such as "Open the
credential creation form" or "Submit the credential creation form". Secure Capture is step
metadata, not part of the objective. The secure-capture action selector must target the control
that creates the credential; the secure field and provider ID selectors identify the resulting
output. The one irreversible revocation step must use browser.revokeCredential; ordinary setup
clicks use browser.click. Use build_playbook to canonicalise the candidate and validate_playbook
before returning it. Never put
secret values in a playbook or response.""",
    tools=[analyse_walkthrough, build_playbook, validate_playbook],
    output_schema=PlaybookAgentDraft,
    output_key="playbook_draft",
    mode="chat",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="uumi_playbook", root_agent=root_agent)
agent_app = managed_app(app, {"build_playbook", "analyse_walkthrough", "validate_playbook"})
