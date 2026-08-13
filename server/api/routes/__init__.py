from api.routes.approvals import router as approvals_router
from api.routes.health import router as health_router
from api.routes.incidents import router as incidents_router
from api.routes.inventory import router as inventory_router
from api.routes.playbooks import router as playbooks_router
from api.routes.runs import router as runs_router

__all__ = [
    "approvals_router",
    "health_router",
    "incidents_router",
    "inventory_router",
    "playbooks_router",
    "runs_router",
]
