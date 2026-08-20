from datetime import UTC, datetime

import pytest
from contracts import (
    Application,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    CredentialGeneration,
    Environment,
    GenerationState,
    ManagedCredential,
    PageCheckpoint,
    Playbook,
    PlaybookDraft,
    PlaybookState,
    PlaybookStep,
    PlaybookVersion,
    SecureField,
    Selector,
    SelectorKind,
    Stage,
)
from core.audit import GENESIS, event_hash
from core.errors import PlaybookError, ResourceConflictError
from core.inventory import InventoryService
from core.playbook import PlaybookService
from policy import digest
from testkit import make_http_provider_api

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Catalog:
    def __init__(self) -> None:
        self.version: PlaybookVersion | None = None
        self.connection_values: dict[str, Connection] = {
            "provider_one": _api_connection("provider_one", ConnectionRole.PROVIDER),
            "secret_one": _api_connection("secret_one", ConnectionRole.SECRET_STORE),
            "runtime_one": _api_connection("runtime_one", ConnectionRole.RUNTIME),
        }

    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: PlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[Playbook, PlaybookVersion]:
        root = Playbook(
            id=playbook_id,
            organisation_id=organisation_id,
            name=definition.name,
            platform=definition.platform,
            latest_version=1,
            created_at=created_at,
            updated_at=created_at,
        )
        self.version = PlaybookVersion(
            id=version_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            number=1,
            definition=definition,
            digest=definition_digest,
            state=PlaybookState.DRAFT,
            source_ids=source_ids,
            created_by=actor_id,
            created_at=created_at,
        )
        return root, self.version

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        return ()

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        assert self.version is not None
        return self.version

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> PlaybookVersion:
        assert self.version is not None
        self.version = self.version.model_copy(
            update={
                "state": PlaybookState.PUBLISHED,
                "published_by": actor_id,
                "published_at": published_at,
            }
        )
        return self.version

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return self.connection_values[resource_id]

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection:
        connection = self.connection_values[connection_id]
        assert connection.revision == expected_revision
        connection = connection.model_copy(
            update={
                "playbook_id": playbook_id,
                "playbook_version_id": version_id,
                "status": ConnectionStatus.READY,
                "updated_at": updated_at,
                "revision": expected_revision + 1,
            }
        )
        self.connection_values[connection_id] = connection
        return connection

    async def add_connection(self, value: Connection) -> Connection:
        self.connection_values[value.id] = value
        return value

    async def add_application(self, value: Application) -> Application:
        return value

    async def add_environment(self, value: Environment) -> Environment:
        return value

    async def add_service(self, value: ConsumerService) -> ConsumerService:
        return value

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]:
        return application, environment, service

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return Application(
            id=resource_id,
            organisation_id=organisation_id,
            display_name="Application one",
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return Environment(
            id=resource_id,
            organisation_id=organisation_id,
            application_id="application_one",
            display_name="Production",
            production=True,
            region="us-east1",
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        if resource_id != "service_one":
            raise ResourceConflictError("service not found")
        return ConsumerService(
            id="service_one",
            organisation_id=organisation_id,
            application_id="application_one",
            environment_id="environment_one",
            runtime_connection_id="runtime_one",
            runtime_resource="projects/project-one/locations/us-east1/services/service-one",
            display_name="Service one",
            identity="service-one@example.iam.gserviceaccount.com",
            created_at=NOW,
            updated_at=NOW,
        )

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        raise NotImplementedError

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
    ) -> ManagedCredential:
        return credential

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return ()

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return tuple(self.connection_values.values())

    async def applications(self, organisation_id: str) -> tuple[Application, ...]:
        return ()

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return ()

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return ()

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return ()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_playbook_publishes_then_attaches_to_browser_connection() -> None:
    repository = Catalog()
    repository.connection_values["browser_one"] = _browser_connection()
    service = PlaybookService(repository, clock=lambda: NOW, inventory=repository)
    _, version = await service.create_version(
        "org_one", "playbook_one", "version_one", _draft(), "author_one"
    )

    with pytest.raises(PlaybookError, match="published"):
        await service.attach("org_one", "browser_one", 0, "playbook_one", version.id)

    published = await service.publish("org_one", "playbook_one", version.id, "admin_one")
    attached = await service.attach("org_one", "browser_one", 0, "playbook_one", published.id)

    assert published.state is PlaybookState.PUBLISHED
    assert attached.playbook_version_id == version.id
    assert attached.status is ConnectionStatus.READY


@pytest.mark.anyio
async def test_playbook_rejects_checkpoints_outside_its_domains() -> None:
    service = PlaybookService(Catalog(), clock=lambda: NOW)
    escaped = _draft().model_copy(update={"login_url_pattern": "https://untrusted.example/login"})

    with pytest.raises(PlaybookError, match="escapes"):
        await service.create_version(
            "org_one", "playbook_one", "version_one", escaped, "author_one"
        )


@pytest.mark.anyio
async def test_inventory_rejects_missing_consumer_binding() -> None:
    repository = Catalog()
    service = InventoryService(repository)
    credential = ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_reference="projects/project-one/secrets/key",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        policy_version="policy_one",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_one",
        organisation_id="org_one",
        credential_id=credential.id,
        state=GenerationState.ACTIVE,
        attempt_id="attempt_one",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )

    with pytest.raises(ResourceConflictError, match="must match"):
        await service.import_credential(credential, generation, ())


@pytest.mark.anyio
async def test_service_runtime_must_stay_inside_its_connection_boundary() -> None:
    service = InventoryService(Catalog())
    escaped = ConsumerService(
        id="service_one",
        organisation_id="org_one",
        application_id="application_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="projects/another-project/locations/us-east1/services/service-one",
        display_name="Service one",
        identity="service-one@example.iam.gserviceaccount.com",
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ResourceConflictError, match="escapes"):
        await service.add_service(escaped)


@pytest.mark.anyio
async def test_application_setup_validates_relationships_before_atomic_write() -> None:
    repository = Catalog()
    inventory = InventoryService(repository)
    application = await repository.get_application("org_one", "application_one")
    environment = await repository.get_environment("org_one", "environment_one")
    consumer = await repository.get_service("org_one", "service_one")

    created = await inventory.add_application_setup(application, environment, consumer)

    assert created == (application, environment, consumer)
    with pytest.raises(ResourceConflictError, match="does not belong"):
        await inventory.add_application_setup(
            application,
            environment.model_copy(update={"application_id": "application_other"}),
            consumer,
        )


@pytest.mark.anyio
async def test_browser_connection_requires_domains_and_capability() -> None:
    service = InventoryService(Catalog())
    browser = _browser_connection().model_copy(update={"allowed_resources": ("invalid",)})
    with pytest.raises(ResourceConflictError, match="allowed domains"):
        await service.add_connection(browser)

    without_capability = _browser_connection().model_copy(
        update={"capabilities": frozenset({"provider.createCredential"})}
    )
    with pytest.raises(ResourceConflictError, match="browser capability"):
        await service.add_connection(without_capability)


def test_audit_hash_binds_sequence_and_previous_event() -> None:
    first = event_hash(
        "org_one",
        0,
        "credential.imported",
        "user_one",
        "credentials/credential_one",
        None,
        {"provider": "vendor"},
        (),
        GENESIS,
        NOW,
        "us-east1",
    )
    second = event_hash(
        "org_one",
        1,
        "credential.updated",
        "user_one",
        "credentials/credential_one",
        None,
        {"provider": "vendor"},
        (),
        first,
        NOW,
        "us-east1",
    )
    assert first != second


def test_legacy_connections_migrate_to_the_explicit_connection_model() -> None:
    connection = Connection.model_validate(
        {
            "id": "connection_provider",
            "organisation_id": "org_one",
            "kind": "provider",
            "provider": "vendor",
            "display_name": "Vendor API",
            "auth_reference": "projects/p/secrets/admin/versions/1",
            "capabilities": ["provider.listCredentialMetadata"],
            "allowed_resources": ["account-one"],
            "http": make_http_provider_api().model_dump(mode="json"),
            "status": "ready",
            "region": "us-east1",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    assert connection.platform == "vendor"
    assert connection.roles == frozenset({ConnectionRole.PROVIDER})
    assert connection.interface is ConnectionInterface.API
    assert connection.authorization is ConnectionAuthorization.API_KEY


def _browser_connection() -> Connection:
    return Connection(
        id="browser_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        authorization_reference="projects/p/secrets/session/versions/1",
        capabilities=frozenset({"browser.execute", "browser.authenticate"}),
        allowed_resources=("vendor.example.com", "*.vendor.example.com"),
        status=ConnectionStatus.SETUP_REQUIRED,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _api_connection(identifier: str, role: ConnectionRole) -> Connection:
    platform = {
        ConnectionRole.PROVIDER: "provider",
        ConnectionRole.SECRET_STORE: "google-secret-manager",
        ConnectionRole.RUNTIME: "cloud-run",
    }[role]
    allowed = {
        ConnectionRole.PROVIDER: ("account-one",),
        ConnectionRole.SECRET_STORE: ("projects/project-one/secrets",),
        ConnectionRole.RUNTIME: ("projects/project-one/locations/us-east1/services",),
    }[role]
    return Connection(
        id=identifier,
        organisation_id="org_one",
        platform=platform,
        display_name=identifier,
        roles=frozenset({role}),
        interface=ConnectionInterface.API,
        authorization=(
            ConnectionAuthorization.API_KEY
            if role is ConnectionRole.PROVIDER
            else ConnectionAuthorization.WORKLOAD_IDENTITY
        ),
        authorization_reference=(
            "projects/project-one/secrets/provider-admin/versions/1"
            if role is ConnectionRole.PROVIDER
            else "workload-identity://service"
        ),
        capabilities=frozenset({f"{role.value}.operate"}),
        allowed_resources=allowed,
        http=make_http_provider_api() if role is ConnectionRole.PROVIDER else None,
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _draft() -> PlaybookDraft:
    checkpoint = PageCheckpoint(url_pattern="https://vendor.example.com/keys")
    selector = Selector(kind=SelectorKind.TEST_ID, value="key-control")
    return PlaybookDraft(
        name="Vendor browser rotation",
        platform="vendor",
        allowed_domains=("vendor.example.com",),
        login_url_pattern="https://vendor.example.com/login",
        steps=(
            PlaybookStep(
                id="create_key",
                stage=Stage.CREATE,
                tool="browser.secure-capture",
                operation="capture",
                objective="Create and capture the replacement credential",
                selectors=(selector,),
                checkpoint=checkpoint,
                secure_field=SecureField(
                    name="api_key",
                    selector=selector,
                    provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="key-id"),
                ),
                evidence_checks=frozenset({"captured"}),
            ),
            PlaybookStep(
                id="revoke_key",
                stage=Stage.REVOKE,
                tool="browser.click",
                operation="revoke",
                objective="Revoke the prior credential",
                selectors=(selector,),
                checkpoint=checkpoint,
                evidence_checks=frozenset({"revoked"}),
            ),
        ),
    )


def test_playbook_digest_matches_immutable_definition() -> None:
    assert len(digest(_draft())) == 64
