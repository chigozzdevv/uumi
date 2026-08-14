from collections.abc import Awaitable, Callable

from core.errors import (
    ActiveRunConflictError,
    AuthenticationError,
    AuthorizationError,
    FireKeyError,
    IdempotencyConflictError,
    LeaseConflictError,
    ResourceConflictError,
    ResourceNotFoundError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
    TransitionRejectedError,
)
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from policy import PolicyViolationError
from telemetry import instrument

from api.deps import ApiServices, build_services
from api.routes import (
    agents_router,
    approvals_router,
    browsers_router,
    health_router,
    incidents_router,
    inventory_router,
    notifications_router,
    playbooks_router,
    policies_router,
    probes_router,
    runs_router,
    walkthroughs_router,
)

ErrorHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def create_app(services: ApiServices | None = None) -> FastAPI:
    app = FastAPI(title="FireKey", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.services = services or build_services()
    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(inventory_router)
    app.include_router(notifications_router)
    app.include_router(playbooks_router)
    app.include_router(policies_router)
    app.include_router(probes_router)
    app.include_router(approvals_router)
    app.include_router(agents_router)
    app.include_router(browsers_router)
    app.include_router(incidents_router)
    app.include_router(walkthroughs_router)
    app.add_exception_handler(FireKeyError, _firekey_error)
    app.add_exception_handler(PolicyViolationError, _policy_error)
    instrument(app, "firekey-api")
    return app


async def _firekey_error(request: Request, error: Exception) -> JSONResponse:
    del request
    if isinstance(error, AuthenticationError):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated", str(error))
    if isinstance(error, AuthorizationError):
        return _error(status.HTTP_403_FORBIDDEN, "forbidden", str(error))
    if isinstance(error, RunNotFoundError | ResourceNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, "not-found", str(error))
    if isinstance(
        error,
        (
            ActiveRunConflictError,
            IdempotencyConflictError,
            LeaseConflictError,
            RevisionConflictError,
            ResourceConflictError,
        ),
    ):
        return _error(status.HTTP_409_CONFLICT, "conflict", str(error))
    if isinstance(error, TransitionRejectedError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "transition-rejected", str(error))
    if isinstance(error, StorageIntegrityError):
        return _error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "storage-integrity-failure",
            "stored run data failed an integrity check",
        )
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal-error",
        "an internal FireKey error occurred",
    )


async def _policy_error(request: Request, error: Exception) -> JSONResponse:
    del request
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "policy-rejected", str(error))


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})
