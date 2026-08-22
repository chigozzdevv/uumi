from uuid import uuid4

from browser.setup import BrowserSetupApi
from contracts import (
    Application,
    Connection,
    ConsumerBinding,
    ConsumerService,
    Contract,
    ControlPreferences,
    ControlVersion,
    CredentialGeneration,
    Environment,
    FunctionalVerification,
    Identifier,
    ManagedCredential,
    ProviderCredentialMetadata,
    RuntimeResourceMetadata,
    SecretResourceMetadata,
    SecretVersionMetadata,
    SetupSession,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Query, Request, Response, status
from pydantic import AwareDatetime, Field, model_validator

from api.deps import ApiServices, Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/inventory",
    tags=["inventory"],
)


class ImportCredentialRequest(Contract):
    credential: ManagedCredential
    generation: CredentialGeneration
    bindings: tuple[ConsumerBinding, ...]
    controls: ControlPreferences


class InventoryGraph(Contract):
    credentials: tuple[ManagedCredential, ...]
    services: tuple[ConsumerService, ...]
    bindings: tuple[ConsumerBinding, ...]


class ApplicationSetupRequest(Contract):
    application: Application
    environment: Environment
    service: ConsumerService


class ApplicationSetupResponse(ApplicationSetupRequest):
    pass


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


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=0)


class ArchiveRequest(RevisionRequest):
    cascade: bool = False


class ConnectionUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    capabilities: frozenset[str] | None = Field(default=None, min_length=1)
    allowed_resources: tuple[str, ...] | None = Field(default=None, min_length=1)
    region: str | None = Field(default=None, min_length=3, max_length=32)

    @model_validator(mode="after")
    def require_change(self) -> "ConnectionUpdateRequest":
        return _changed(self)


class ApplicationUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    repository_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ApplicationUpdateRequest":
        return _changed(self)


class EnvironmentUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    production: bool | None = None
    region: str | None = Field(default=None, min_length=3, max_length=32)

    @model_validator(mode="after")
    def require_change(self) -> "EnvironmentUpdateRequest":
        return _changed(self)


class ServiceUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    runtime_connection_id: Identifier | None = None
    telemetry_connection_ids: tuple[Identifier, ...] | None = None
    runtime_resource: str | None = Field(default=None, min_length=1, max_length=512)
    verification: FunctionalVerification | None = None
    repository: str | None = Field(default=None, min_length=1, max_length=256)
    identity: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "ServiceUpdateRequest":
        return _changed(self)


class CredentialUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_change(self) -> "CredentialUpdateRequest":
        return _changed(self)


class ControlsUpdateRequest(RevisionRequest):
    version_id: Identifier
    controls: ControlPreferences


class CredentialControlsResponse(Contract):
    credential: ManagedCredential
    controls: ControlVersion


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


@router.post(
    "/application-setups",
    response_model=ApplicationSetupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_application_setup(
    organisation_id: Identifier,
    body: ApplicationSetupRequest,
    identity: Identity,
    request: Request,
) -> ApplicationSetupResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    for value in (body.application, body.environment, body.service):
        _organisation(value.organisation_id, organisation_id)
    application, environment, service = await required(
        api.inventory, "inventory"
    ).add_application_setup(body.application, body.environment, body.service)
    return ApplicationSetupResponse(
        application=application,
        environment=environment,
        service=service,
    )


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
        body.credential, body.generation, body.bindings, body.controls, identity.actor_id
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


@router.get("/connections/{connection_id}", response_model=Connection)
async def get_connection(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_connection(organisation_id, connection_id)


@router.get(
    "/connections/{connection_id}/credential-metadata",
    response_model=tuple[ProviderCredentialMetadata, ...],
)
async def list_provider_credentials(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[ProviderCredentialMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_provider_credentials(
        organisation_id, connection_id
    )


@router.get(
    "/connections/{connection_id}/runtime-resources",
    response_model=tuple[RuntimeResourceMetadata, ...],
)
async def list_runtime_resources(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[RuntimeResourceMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_runtime_resources(
        organisation_id, connection_id
    )


@router.get(
    "/connections/{connection_id}/secret-resources",
    response_model=tuple[SecretResourceMetadata, ...],
)
async def list_secret_resources(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[SecretResourceMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_secret_resources(
        organisation_id, connection_id
    )


@router.get(
    "/connections/{connection_id}/secret-versions",
    response_model=tuple[SecretVersionMetadata, ...],
)
async def list_secret_versions(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
    secret: str = Query(min_length=1, max_length=1024),
) -> tuple[SecretVersionMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_secret_versions(
        organisation_id, connection_id, secret
    )


@router.patch("/connections/{connection_id}", response_model=Connection)
async def update_connection(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: ConnectionUpdateRequest,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_connection(
        organisation_id,
        connection_id,
        body.expected_revision,
        display_name=body.display_name,
        capabilities=body.capabilities,
        allowed_resources=body.allowed_resources,
        region=body.region,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "connection.updated",
        connection_id,
        changed.revision,
    )
    return changed


@router.post("/connections/{connection_id}/archive", response_model=Connection)
async def archive_connection(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_connection(
        organisation_id, connection_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "connection.archived",
        connection_id,
        changed.revision,
    )
    return changed


@router.get("/applications", response_model=tuple[Application, ...])
async def list_applications(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Application, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_applications(organisation_id)


@router.get("/applications/{application_id}", response_model=Application)
async def get_application(
    organisation_id: Identifier,
    application_id: Identifier,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_application(
        organisation_id, application_id
    )


@router.patch("/applications/{application_id}", response_model=Application)
async def update_application(
    organisation_id: Identifier,
    application_id: Identifier,
    body: ApplicationUpdateRequest,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_application(
        organisation_id,
        application_id,
        body.expected_revision,
        display_name=body.display_name,
        repository_ids=body.repository_ids,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "application.updated",
        application_id,
        changed.revision,
    )
    return changed


@router.post("/applications/{application_id}/archive", response_model=Application)
async def archive_application(
    organisation_id: Identifier,
    application_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> Application:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_application(
        organisation_id, application_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "application.archived",
        application_id,
        changed.revision,
    )
    return changed


@router.get("/environments", response_model=tuple[Environment, ...])
async def list_environments(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Environment, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_environments(organisation_id)


@router.get("/environments/{environment_id}", response_model=Environment)
async def get_environment(
    organisation_id: Identifier,
    environment_id: Identifier,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_environment(
        organisation_id, environment_id
    )


@router.patch("/environments/{environment_id}", response_model=Environment)
async def update_environment(
    organisation_id: Identifier,
    environment_id: Identifier,
    body: EnvironmentUpdateRequest,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_environment(
        organisation_id,
        environment_id,
        body.expected_revision,
        display_name=body.display_name,
        production=body.production,
        region=body.region,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "environment.updated",
        environment_id,
        changed.revision,
    )
    return changed


@router.post("/environments/{environment_id}/archive", response_model=Environment)
async def archive_environment(
    organisation_id: Identifier,
    environment_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> Environment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_environment(
        organisation_id, environment_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "environment.archived",
        environment_id,
        changed.revision,
    )
    return changed


@router.get("/services", response_model=tuple[ConsumerService, ...])
async def list_services(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[ConsumerService, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_services(organisation_id)


@router.get("/services/{service_id}", response_model=ConsumerService)
async def get_service(
    organisation_id: Identifier,
    service_id: Identifier,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_service(organisation_id, service_id)


@router.patch("/services/{service_id}", response_model=ConsumerService)
async def update_service(
    organisation_id: Identifier,
    service_id: Identifier,
    body: ServiceUpdateRequest,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_service(
        organisation_id,
        service_id,
        body.expected_revision,
        display_name=body.display_name,
        runtime_connection_id=body.runtime_connection_id,
        telemetry_connection_ids=body.telemetry_connection_ids,
        runtime_resource=body.runtime_resource,
        verification=body.verification,
        repository=body.repository,
        identity=body.identity,
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "service.updated", service_id, changed.revision
    )
    return changed


@router.post("/services/{service_id}/archive", response_model=ConsumerService)
async def archive_service(
    organisation_id: Identifier,
    service_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> ConsumerService:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_service(
        organisation_id, service_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "service.archived", service_id, changed.revision
    )
    return changed


@router.get("/credentials", response_model=tuple[ManagedCredential, ...])
async def list_credentials(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[ManagedCredential, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_credentials(organisation_id)


@router.get("/credentials/{credential_id}", response_model=ManagedCredential)
async def get_credential(
    organisation_id: Identifier,
    credential_id: Identifier,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_credential(organisation_id, credential_id)


@router.patch("/credentials/{credential_id}", response_model=ManagedCredential)
async def update_credential(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: CredentialUpdateRequest,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").update_credential(
        organisation_id,
        credential_id,
        body.expected_revision,
        display_name=body.display_name,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "credential.updated",
        credential_id,
        changed.revision,
    )
    return changed


@router.get(
    "/credentials/{credential_id}/controls/{version_id}",
    response_model=ControlVersion,
)
async def get_credential_controls(
    organisation_id: Identifier,
    credential_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> ControlVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_controls(
        organisation_id, credential_id, version_id
    )


@router.post(
    "/credentials/{credential_id}/controls",
    response_model=CredentialControlsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def update_credential_controls(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: ControlsUpdateRequest,
    identity: Identity,
    request: Request,
) -> CredentialControlsResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    credential, controls = await required(api.inventory, "inventory").update_controls(
        organisation_id,
        credential_id,
        body.expected_revision,
        body.version_id,
        body.controls,
        identity.actor_id,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "credential.controls.updated",
        credential_id,
        credential.revision,
    )
    return CredentialControlsResponse(credential=credential, controls=controls)


@router.post("/credentials/{credential_id}/archive", response_model=ManagedCredential)
async def archive_credential(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    changed = await required(api.inventory, "inventory").archive_credential(
        organisation_id, credential_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "credential.archived",
        credential_id,
        changed.revision,
    )
    return changed


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


def _changed[T: RevisionRequest](value: T) -> T:
    if value.model_fields_set == {"expected_revision"}:
        raise ValueError("at least one editable field is required")
    return value


async def _audit_change(
    api: ApiServices,
    organisation_id: str,
    actor_id: str,
    kind: str,
    resource_id: str,
    revision: int,
) -> None:
    if api.audit is None:
        return
    await api.audit.append(
        f"audit_{uuid4().hex}",
        organisation_id,
        kind,
        actor_id,
        f"inventory/{resource_id}",
        {"revision": revision},
    )
