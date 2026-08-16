from browser.setup import BrowserSetupApi
from contracts import (
    Application,
    Connection,
    ConsumerBinding,
    ConsumerService,
    Contract,
    CredentialGeneration,
    Environment,
    Identifier,
    ManagedCredential,
    SetupSession,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Request, Response, status
from pydantic import AwareDatetime, Field

from api.deps import ApiServices, Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/inventory",
    tags=["inventory"],
)


class ImportCredentialRequest(Contract):
    credential: ManagedCredential
    generation: CredentialGeneration
    bindings: tuple[ConsumerBinding, ...]


class InventoryGraph(Contract):
    credentials: tuple[ManagedCredential, ...]
    services: tuple[ConsumerService, ...]
    bindings: tuple[ConsumerBinding, ...]


class BeginSetupRequest(Contract):
    secret_container: str = Field(
        pattern=r"^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+$", max_length=1024
    )
    extra_domains: tuple[str, ...] = Field(default=(), max_length=20)


class BeginSetupResponse(Contract):
    session: SetupSession
    token: str = Field(min_length=32)
    gateway_url: str = Field(min_length=12)
    expires_at: AwareDatetime


class CompleteSetupRequest(Contract):
    expected_revision: int = Field(ge=0)
    token: str = Field(min_length=32, max_length=256)


class CompleteSetupResponse(Contract):
    session: SetupSession
    connection: Connection
    resumed_run_ids: tuple[Identifier, ...] = ()


class AbortSetupRequest(Contract):
    expected_revision: int = Field(ge=0)


@router.post("/connections", response_model=Connection, status_code=status.HTTP_201_CREATED)
async def add_connection(
    organisation_id: Identifier,
    body: Connection,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_connection(body)


@router.post("/applications", response_model=Application, status_code=status.HTTP_201_CREATED)
async def add_application(
    organisation_id: Identifier,
    body: Application,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_application(body)


@router.post("/environments", response_model=Environment, status_code=status.HTTP_201_CREATED)
async def add_environment(
    organisation_id: Identifier,
    body: Environment,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_environment(body)


@router.post("/services", response_model=ConsumerService, status_code=status.HTTP_201_CREATED)
async def add_service(
    organisation_id: Identifier,
    body: ConsumerService,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_service(body)


@router.post("/credentials", response_model=ManagedCredential)
async def import_credential(
    organisation_id: Identifier,
    body: ImportCredentialRequest,
    identity: Identity,
    request: Request,
    response: Response,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.credential.organisation_id, organisation_id)
    _organisation(body.generation.organisation_id, organisation_id)
    for binding in body.bindings:
        _organisation(binding.organisation_id, organisation_id)
    result = await required(api.inventory, "inventory").import_credential(
        body.credential, body.generation, body.bindings
    )
    response.status_code = status.HTTP_201_CREATED
    return result


@router.get("/connections", response_model=tuple[Connection, ...])
async def list_connections(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Connection, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_connections(organisation_id)


@router.get("/applications", response_model=tuple[Application, ...])
async def list_applications(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Application, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_applications(organisation_id)


@router.get("/environments", response_model=tuple[Environment, ...])
async def list_environments(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Environment, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_environments(organisation_id)


@router.get("/graph", response_model=InventoryGraph)
async def graph(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> InventoryGraph:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    credentials, consumer_services, bindings = await required(api.inventory, "inventory").graph(
        organisation_id
    )
    return InventoryGraph(
        credentials=credentials,
        services=consumer_services,
        bindings=bindings,
    )


@router.post(
    "/connections/{connection_id}/setup",
    response_model=BeginSetupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def begin_setup(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: BeginSetupRequest,
    identity: Identity,
    request: Request,
) -> BeginSetupResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    setup = _setup(api)
    session, token = await setup.begin(
        organisation_id,
        connection_id,
        body.secret_container,
        identity.subject,
        body.extra_domains,
    )
    return BeginSetupResponse(
        session=session,
        token=token,
        gateway_url=setup.gateway_url,
        expires_at=session.expires_at,
    )


@router.get("/setups/{setup_id}", response_model=SetupSession)
async def get_setup(
    organisation_id: Identifier,
    setup_id: Identifier,
    identity: Identity,
    request: Request,
) -> SetupSession:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await _setup(api).get(organisation_id, setup_id)


@router.post("/setups/{setup_id}/complete", response_model=CompleteSetupResponse)
async def complete_setup(
    organisation_id: Identifier,
    setup_id: Identifier,
    body: CompleteSetupRequest,
    identity: Identity,
    request: Request,
) -> CompleteSetupResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    session, connection, resumed = await _setup(api).complete(
        organisation_id,
        setup_id,
        body.expected_revision,
        body.token,
        identity.subject,
        identity.actor_id,
    )
    return CompleteSetupResponse(session=session, connection=connection, resumed_run_ids=resumed)


@router.post("/setups/{setup_id}/abort", response_model=SetupSession)
async def abort_setup(
    organisation_id: Identifier,
    setup_id: Identifier,
    body: AbortSetupRequest,
    identity: Identity,
    request: Request,
) -> SetupSession:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await _setup(api).abort(
        organisation_id,
        setup_id,
        body.expected_revision,
        identity.subject,
    )


def _organisation(actual: str, expected: str) -> None:
    if actual != expected:
        raise ResourceConflictError("request body crosses organisation boundary")


def _setup(api: ApiServices) -> BrowserSetupApi:
    if api.browser_setup is None:
        raise ResourceConflictError("browser setup is not configured")
    return api.browser_setup
