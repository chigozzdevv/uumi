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
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Request, Response, status

from api.deps import Identity, required, services

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


def _organisation(actual: str, expected: str) -> None:
    if actual != expected:
        raise ResourceConflictError("request body crosses organisation boundary")
