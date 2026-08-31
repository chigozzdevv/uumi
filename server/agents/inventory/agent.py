from google.adk.agents import Agent
from google.adk.apps import App

from agents.shared.app import managed_app
from agents.shared.model import managed_model
from agents.shared.models import InventoryAssessment
from agents.shared.tools import inspect_inventory

root_agent = Agent(
    name="inventory_exposure_agent",
    model=managed_model(),
    description="Correlates credential exposure with Uumi's inventory graph.",
    instruction="""Call inspect_inventory exactly once. It executes the immutable skill bound by
the Uumi control plane. Copy its complete result exactly. Never request or infer credential values
or choose a different inventory operation. Return conclusions as structured JSON.""",
    tools=[inspect_inventory],
    output_schema=InventoryAssessment,
    output_key="inventory_assessment",
    mode="chat",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
app = App(name="uumi_inventory", root_agent=root_agent)
agent_app = managed_app(
    app,
    {"correlate_exposure", "resolve_consumers", "detect_stale_mapping"},
)
