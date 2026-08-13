from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared import correlate_exposure, detect_stale_mapping, resolve_consumers
from agents.shared.app import managed_app
from agents.shared.models import InventoryAssessment

root_agent = Agent(
    name="inventory_exposure_agent",
    model="gemini-3.5-flash",
    description="Correlates credential exposure with FireKey's inventory graph.",
    instruction="""Use only the registered read tools and managed session state. Correlate the
incident with declared consumers, call out stale or missing mappings, and cite returned resource
IDs. Never request or infer credential values. Return conclusions as structured JSON.""",
    tools=[correlate_exposure, resolve_consumers, detect_stale_mapping],
    output_schema=InventoryAssessment,
    output_key="inventory_assessment",
    mode="task",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="firekey_inventory", root_agent=root_agent)
agent_app = managed_app(
    app,
    {"correlate_exposure", "resolve_consumers", "detect_stale_mapping"},
)
