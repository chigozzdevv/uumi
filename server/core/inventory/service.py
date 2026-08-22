from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from contracts import (
    Application,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlPreferences,
    ControlVersion,
    CredentialGeneration,
    Environment,
    FunctionalVerification,
    ManagedCredential,
    PlaybookVersion,
    ProbeVersion,
    ProviderCredentialMetadata,
    RuntimeResourceMetadata,
    SecretResourceMetadata,
    SecretVersionMetadata,
)
from policy import digest

from core.errors import ResourceConflictError
from core.inventory.controls import compile_controls
from core.inventory.controls import update_controls as compile_update

_BROWSER_CAPABILITIES = frozenset({"browser.execute", "browser.authenticate"})
InventoryResource = Connection | Application | Environment | ConsumerService | ManagedCredential


class InventoryRepository(Protocol):
    async def add_connection(self, value: Connection) -> Connection: ...

    async def add_application(self, value: Application) -> Application: ...

    async def add_environment(self, value: Environment) -> Environment: ...

    async def add_service(self, value: ConsumerService) -> ConsumerService: ...

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]: ...

    async def get_application(self, organisation_id: str, resource_id: str) -> Application: ...

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment: ...

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService: ...

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential: ...

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion: ...

    async def get_playbook_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion: ...

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection: ...

    async def replace_application(
        self, value: Application, expected_revision: int
    ) -> Application: ...

    async def replace_environment(
        self, value: Environment, expected_revision: int
    ) -> Environment: ...

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService: ...

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential: ...

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection: ...

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection: ...

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
    ) -> ManagedCredential: ...

    async def replace_controls(
        self,
        credential: ManagedCredential,
        expected_revision: int,
        controls: ControlVersion,
    ) -> tuple[ManagedCredential, ControlVersion]: ...

    async def archive_inventory(
        self,
        resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ],
        bindings: tuple[ConsumerBinding, ...],
    ) -> None: ...

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]: ...

    async def applications(self, organisation_id: str) -> tuple[Application, ...]: ...

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]: ...

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]: ...

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]: ...


class ProviderMetadataLister(Protocol):
    async def metadata(self, connection: Connection) -> tuple[dict[str, object], ...]: ...


class SecretMetadataLister(Protocol):
    async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]: ...

    async def versions_for(
        self, connection: Connection, secret: str
    ) -> tuple[dict[str, object], ...]: ...


class RuntimeMetadataLister(Protocol):
    async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]: ...


class InventoryService:
    def __init__(
        self,
        repository: InventoryRepository,
        clock: Callable[[], datetime] | None = None,
        provider_metadata: ProviderMetadataLister | None = None,
        secret_metadata: SecretMetadataLister | None = None,
        runtime_metadata: RuntimeMetadataLister | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._provider_metadata = provider_metadata
        self._secret_metadata = secret_metadata
        self._runtime_metadata = runtime_metadata

    async def add_connection(self, connection: Connection) -> Connection:
        if connection.interface is ConnectionInterface.BROWSER:
            if not connection.allowed_resources or any(
                not _domain_pattern(value) for value in connection.allowed_resources
            ):
                raise ResourceConflictError("browser connection must declare allowed domains")
            if not connection.capabilities.intersection(_BROWSER_CAPABILITIES):
                raise ResourceConflictError("browser connection must declare a browser capability")
            if connection.authorization is not ConnectionAuthorization.BROWSER_SESSION:
                raise ResourceConflictError(
                    "browser connection requires browser-session authorization"
                )
        if (
            ConnectionRole.PROVIDER in connection.roles
            and connection.interface is ConnectionInterface.API
            and connection.http is None
        ):
            raise ResourceConflictError("provider connection requires an HTTP API declaration")
        return await self._repository.add_connection(connection)

    async def add_application(self, application: Application) -> Application:
        return await self._repository.add_application(application)

    async def add_environment(self, environment: Environment) -> Environment:
        application = await self._repository.get_application(
            environment.organisation_id,
            environment.application_id,
        )
        _tenant(application.organisation_id, environment.organisation_id)
        return await self._repository.add_environment(environment)

    async def add_service(self, service: ConsumerService) -> ConsumerService:
        environment = await self._repository.get_environment(
            service.organisation_id,
            service.environment_id,
        )
        if environment.application_id != service.application_id:
            raise ResourceConflictError("service application does not match its environment")
        await self._validate_service_connections(service)
        return await self._repository.add_service(service)

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]:
        _tenant(application.organisation_id, environment.organisation_id)
        _tenant(application.organisation_id, service.organisation_id)
        if environment.application_id != application.id:
            raise ResourceConflictError("environment does not belong to the application")
        if service.application_id != application.id or service.environment_id != environment.id:
            raise ResourceConflictError("service does not belong to the application environment")
        await self._validate_service_connections(service)
        return await self._repository.add_application_setup(application, environment, service)

    async def _validate_service_connections(self, service: ConsumerService) -> None:
        runtime = await self._repository.get_connection(
            service.organisation_id,
            service.runtime_connection_id,
        )
        if (
            ConnectionRole.RUNTIME not in runtime.roles
            or runtime.interface is not ConnectionInterface.API
        ):
            raise ResourceConflictError("service requires an API runtime connection")
        if not _resource_covered(service.runtime_resource, runtime.allowed_resources):
            raise ResourceConflictError("service runtime resource escapes the connection boundary")
        for connection_id in service.telemetry_connection_ids:
            telemetry = await self._repository.get_connection(
                service.organisation_id,
                connection_id,
            )
            if (
                ConnectionRole.TELEMETRY not in telemetry.roles
                or telemetry.interface is not ConnectionInterface.API
            ):
                raise ResourceConflictError("service requires API telemetry connections")

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        preferences: ControlPreferences,
        actor_id: str,
    ) -> ManagedCredential:
        management = await self._repository.get_connection(
            credential.organisation_id,
            credential.connection_id,
        )
        if (
            ConnectionRole.PROVIDER not in management.roles
            or management.platform != credential.provider
            or management.archived_at is not None
            or management.status is not ConnectionStatus.READY
        ):
            raise ResourceConflictError(
                "credential requires a provider connection for the same platform"
            )
        if management.interface is ConnectionInterface.BROWSER and (
            management.playbook_id is None or management.playbook_version_id is None
        ):
            raise ResourceConflictError("browser-managed credentials require a connection playbook")
        secret_store = await self._repository.get_connection(
            credential.organisation_id,
            credential.secret_store_connection_id,
        )
        if (
            ConnectionRole.SECRET_STORE not in secret_store.roles
            or secret_store.interface is not ConnectionInterface.API
            or secret_store.archived_at is not None
            or secret_store.status is not ConnectionStatus.READY
        ):
            raise ResourceConflictError("credential requires an API secret-store connection")
        if not any(
            credential.secret_reference == boundary
            or credential.secret_reference.startswith(boundary.rstrip("/") + "/")
            for boundary in secret_store.allowed_resources
        ):
            raise ResourceConflictError("credential secret reference escapes the secret store")
        if generation.credential_id != credential.id:
            raise ResourceConflictError("generation does not belong to the credential")
        if credential.active_generation_id != generation.id:
            raise ResourceConflictError("imported generation must be the active generation")
        if credential.secret_reference != generation.secret_reference:
            raise ResourceConflictError("credential and active generation secret references differ")
        if "/versions/" not in credential.secret_reference:
            raise ResourceConflictError(
                "credential secret reference must identify one immutable version"
            )
        if len(bindings) != len(credential.consumer_ids) or set(credential.consumer_ids) != {
            binding.service_id for binding in bindings
        }:
            raise ResourceConflictError("credential consumers and bindings must match exactly")
        if any(
            binding.credential_id != credential.id
            or binding.current_generation_id != generation.id
            or binding.organisation_id != credential.organisation_id
            for binding in bindings
        ):
            raise ResourceConflictError("credential binding lineage is inconsistent")
        services: list[ConsumerService] = []
        connections: dict[str, Connection] = {
            connection.id: connection
            for connection in await self._repository.connections(credential.organisation_id)
            if connection.archived_at is None
        }
        connections.update({management.id: management, secret_store.id: secret_store})
        for binding in bindings:
            service = await self._repository.get_service(
                credential.organisation_id,
                binding.service_id,
            )
            if (
                binding.environment_id != service.environment_id
                or binding.runtime_connection_id != service.runtime_connection_id
                or binding.runtime_resource != service.runtime_resource
            ):
                raise ResourceConflictError(
                    "credential binding does not match its consumer service"
                )
            runtime = await self._repository.get_connection(
                credential.organisation_id,
                binding.runtime_connection_id,
            )
            if (
                ConnectionRole.RUNTIME not in runtime.roles
                or runtime.interface is not ConnectionInterface.API
                or runtime.archived_at is not None
                or runtime.status is not ConnectionStatus.READY
            ):
                raise ResourceConflictError("credential binding requires an API runtime connection")
            if binding.secret_reference != generation.secret_reference:
                raise ResourceConflictError("credential binding must use the imported generation")
            services.append(service)
            connections[runtime.id] = runtime
            for connection_id in service.telemetry_connection_ids:
                telemetry = await self._repository.get_connection(
                    credential.organisation_id, connection_id
                )
                if (
                    ConnectionRole.TELEMETRY not in telemetry.roles
                    or telemetry.interface is not ConnectionInterface.API
                    or telemetry.archived_at is not None
                    or telemetry.status is not ConnectionStatus.READY
                ):
                    raise ResourceConflictError(
                        "credential consumer requires ready API telemetry connections"
                    )
                connections[telemetry.id] = telemetry
        now = self._clock()
        definition, probes = compile_controls(
            credential,
            bindings,
            tuple(services),
            tuple(connections.values()),
            preferences,
            actor_id,
            now,
        )
        controls = ControlVersion(
            id=credential.control_version,
            organisation_id=credential.organisation_id,
            credential_id=credential.id,
            number=1,
            definition=definition,
            digest=digest(definition),
            created_by=actor_id,
            created_at=now,
        )
        return await self._repository.import_credential(
            credential, generation, bindings, controls, probes
        )

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return await self._repository.get_connection(organisation_id, resource_id)

    async def list_provider_credentials(
        self, organisation_id: str, connection_id: str
    ) -> tuple[ProviderCredentialMetadata, ...]:
        connection = await self.get_connection(organisation_id, connection_id)
        if connection.archived_at is not None:
            raise ResourceConflictError("archived connection cannot discover credentials")
        if ConnectionRole.PROVIDER not in connection.roles:
            raise ResourceConflictError("credential discovery requires a provider connection")
        if connection.interface is not ConnectionInterface.API:
            raise ResourceConflictError(
                "browser credential discovery must run through its playbook"
            )
        if connection.status is not ConnectionStatus.READY:
            raise ResourceConflictError("provider connection is not ready")
        if "provider.listCredentialMetadata" not in connection.capabilities:
            raise ResourceConflictError("provider connection cannot list credential metadata")
        if self._provider_metadata is None:
            raise ResourceConflictError("provider credential discovery is unavailable")
        metadata = await self._provider_metadata.metadata(connection)
        return tuple(ProviderCredentialMetadata.model_validate(item) for item in metadata)

    async def list_secret_resources(
        self, organisation_id: str, connection_id: str
    ) -> tuple[SecretResourceMetadata, ...]:
        connection = await self._secret_connection(organisation_id, connection_id)
        if self._secret_metadata is None:
            raise ResourceConflictError("secret metadata discovery is unavailable")
        resources = await self._secret_metadata.resources_for(connection)
        return tuple(
            SecretResourceMetadata(
                reference=_required_text(item.get("name"), "secret resource"),
                display_name=_required_text(item.get("name"), "secret resource").rsplit("/", 1)[-1],
            )
            for item in resources
        )

    async def list_runtime_resources(
        self, organisation_id: str, connection_id: str
    ) -> tuple[RuntimeResourceMetadata, ...]:
        connection = await self.get_connection(organisation_id, connection_id)
        if (
            connection.archived_at is not None
            or connection.status is not ConnectionStatus.READY
            or connection.interface is not ConnectionInterface.API
            or ConnectionRole.RUNTIME not in connection.roles
        ):
            raise ResourceConflictError("runtime discovery requires a ready API runtime connection")
        if "runtime.listServices" not in connection.capabilities:
            raise ResourceConflictError("runtime connection cannot list services")
        if self._runtime_metadata is None:
            raise ResourceConflictError("runtime resource discovery is unavailable")
        resources = await self._runtime_metadata.resources_for(connection)
        metadata = tuple(RuntimeResourceMetadata.model_validate(item) for item in resources)
        if any(
            not _resource_covered(resource.reference, connection.allowed_resources)
            for resource in metadata
        ):
            raise ResourceConflictError("runtime discovery escaped the connection boundary")
        return tuple(sorted(metadata, key=lambda item: item.display_name.lower()))

    async def list_secret_versions(
        self, organisation_id: str, connection_id: str, secret: str
    ) -> tuple[SecretVersionMetadata, ...]:
        connection = await self._secret_connection(organisation_id, connection_id)
        if not _resource_covered(secret, connection.allowed_resources):
            raise ResourceConflictError("secret resource escapes the connection boundary")
        if self._secret_metadata is None:
            raise ResourceConflictError("secret metadata discovery is unavailable")
        versions = await self._secret_metadata.versions_for(connection, secret)
        return tuple(
            SecretVersionMetadata.model_validate(
                {
                    "reference": _required_text(item.get("name"), "secret version"),
                    "state": _required_text(item.get("state"), "secret version state"),
                    "created_at": item.get("createTime"),
                }
            )
            for item in versions
            if item.get("state") == "ENABLED"
        )

    async def _secret_connection(self, organisation_id: str, connection_id: str) -> Connection:
        connection = await self.get_connection(organisation_id, connection_id)
        if (
            connection.archived_at is not None
            or connection.status is not ConnectionStatus.READY
            or connection.interface is not ConnectionInterface.API
            or ConnectionRole.SECRET_STORE not in connection.roles
        ):
            raise ResourceConflictError("secret discovery requires a ready secret-store connection")
        return connection

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return await self._repository.get_application(organisation_id, resource_id)

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return await self._repository.get_environment(organisation_id, resource_id)

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        return await self._repository.get_service(organisation_id, resource_id)

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        return await self._repository.get_credential(organisation_id, resource_id)

    async def get_controls(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        credential = await self.get_credential(organisation_id, credential_id)
        controls = await self._repository.get_control_version(
            organisation_id, credential_id, version_id
        )
        if controls.credential_id != credential.id or controls.organisation_id != organisation_id:
            raise ResourceConflictError("controls do not belong to the credential")
        return controls

    async def update_connection(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        capabilities: frozenset[str] | None = None,
        allowed_resources: tuple[str, ...] | None = None,
        region: str | None = None,
    ) -> Connection:
        current = await self.get_connection(organisation_id, resource_id)
        if current.archived_at is not None:
            raise ResourceConflictError("archived connections cannot be edited")
        changed = _change(
            current,
            expected_revision,
            self._clock(),
            display_name=display_name,
            capabilities=capabilities,
            allowed_resources=allowed_resources,
            region=region,
        )
        await self._validate_connection(changed)
        await self._validate_connection_usage(changed)
        return await self._repository.replace_connection(changed, expected_revision)

    async def update_application(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        repository_ids: tuple[str, ...] | None = None,
    ) -> Application:
        current = await self.get_application(organisation_id, resource_id)
        _editable(current, expected_revision, "application")
        changed = _change(
            current,
            expected_revision,
            self._clock(),
            display_name=display_name,
            repository_ids=repository_ids,
        )
        return await self._repository.replace_application(changed, expected_revision)

    async def update_environment(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        production: bool | None = None,
        region: str | None = None,
    ) -> Environment:
        current = await self.get_environment(organisation_id, resource_id)
        _editable(current, expected_revision, "environment")
        changed = _change(
            current,
            expected_revision,
            self._clock(),
            display_name=display_name,
            production=production,
            region=region,
        )
        return await self._repository.replace_environment(changed, expected_revision)

    async def update_service(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        runtime_connection_id: str | None = None,
        telemetry_connection_ids: tuple[str, ...] | None = None,
        runtime_resource: str | None = None,
        verification: FunctionalVerification | None = None,
        repository: str | None = None,
        identity: str | None = None,
    ) -> ConsumerService:
        current = await self.get_service(organisation_id, resource_id)
        _editable(current, expected_revision, "service")
        changed = _change(
            current,
            expected_revision,
            self._clock(),
            display_name=display_name,
            runtime_connection_id=runtime_connection_id,
            telemetry_connection_ids=telemetry_connection_ids,
            runtime_resource=runtime_resource,
            verification=verification,
            repository=repository,
            identity=identity,
        )
        await self._validate_service_connections(changed)
        return await self._repository.replace_service(changed, expected_revision)

    async def update_credential(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
    ) -> ManagedCredential:
        current = await self.get_credential(organisation_id, resource_id)
        _editable(current, expected_revision, "credential")
        changed = _change(
            current,
            expected_revision,
            self._clock(),
            display_name=display_name,
        )
        return await self._repository.replace_credential(changed, expected_revision)

    async def update_controls(
        self,
        organisation_id: str,
        credential_id: str,
        expected_revision: int,
        version_id: str,
        preferences: ControlPreferences,
        actor_id: str,
    ) -> tuple[ManagedCredential, ControlVersion]:
        current = await self.get_credential(organisation_id, credential_id)
        _editable(current, expected_revision, "credential")
        previous = await self.get_controls(organisation_id, credential_id, current.control_version)
        definition = compile_update(previous.definition, preferences)
        controls = ControlVersion(
            id=version_id,
            organisation_id=organisation_id,
            credential_id=credential_id,
            number=previous.number + 1,
            definition=definition,
            digest=digest(definition),
            created_by=actor_id,
            created_at=self._clock(),
        )
        changed = _change(
            current,
            expected_revision,
            self._clock(),
            control_version=controls.id,
        )
        return await self._repository.replace_controls(changed, expected_revision, controls)

    async def archive_connection(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Connection:
        current = await self.get_connection(organisation_id, resource_id)
        credentials = _active(await self._repository.credentials(organisation_id))
        services = _active(await self._repository.services(organisation_id))
        dependent_services = tuple(
            service
            for service in services
            if resource_id == service.runtime_connection_id
            or resource_id in service.telemetry_connection_ids
        )
        dependent_credentials = tuple(
            credential
            for credential in credentials
            if resource_id in {credential.connection_id, credential.secret_store_connection_id}
        )
        if (dependent_services or dependent_credentials) and not cascade:
            raise ResourceConflictError("connection is still used by active inventory")
        return await self._archive_inventory(
            _archive(current, expected_revision, self._clock(), status=ConnectionStatus.DISABLED),
            services=dependent_services,
            credentials=dependent_credentials,
        )

    async def archive_application(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Application:
        current = await self.get_application(organisation_id, resource_id)
        environments = tuple(
            item
            for item in _active(await self._repository.environments(organisation_id))
            if item.application_id == resource_id
        )
        services = tuple(
            item
            for item in _active(await self._repository.services(organisation_id))
            if item.application_id == resource_id
        )
        if (environments or services) and not cascade:
            raise ResourceConflictError(
                "application still contains active environments or services"
            )
        return await self._archive_inventory(
            _archive(current, expected_revision, self._clock()),
            environments=environments,
            services=services,
        )

    async def archive_environment(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Environment:
        current = await self.get_environment(organisation_id, resource_id)
        services = tuple(
            service
            for service in _active(await self._repository.services(organisation_id))
            if service.environment_id == resource_id
        )
        if services and not cascade:
            raise ResourceConflictError("environment still contains active services")
        return await self._archive_inventory(
            _archive(current, expected_revision, self._clock()), services=services
        )

    async def archive_service(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> ConsumerService:
        current = await self.get_service(organisation_id, resource_id)
        bindings = tuple(
            binding
            for binding in await self._repository.bindings(organisation_id)
            if binding.service_id == resource_id
        )
        if bindings and not cascade:
            raise ResourceConflictError("service still has credential bindings")
        return await self._archive_inventory(
            _archive(current, expected_revision, self._clock()),
            credentials=tuple(
                credential
                for credential in _active(await self._repository.credentials(organisation_id))
                if credential.id in {binding.credential_id for binding in bindings}
            ),
        )

    async def archive_credential(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> ManagedCredential:
        current = await self.get_credential(organisation_id, resource_id)
        if current.consumer_ids and not cascade:
            raise ResourceConflictError("credential must be disconnected before it can be archived")
        return await self._archive_inventory(_archive(current, expected_revision, self._clock()))

    async def _archive_inventory[
        T: (Connection, Application, Environment, ConsumerService, ManagedCredential)
    ](
        self,
        primary: T,
        *,
        environments: tuple[Environment, ...] = (),
        services: tuple[ConsumerService, ...] = (),
        credentials: tuple[ManagedCredential, ...] = (),
    ) -> T:
        organisation_id = primary.organisation_id
        all_bindings = await self._repository.bindings(organisation_id)
        service_ids = {service.id for service in services}
        credential_ids = {credential.id for credential in credentials}
        if isinstance(primary, ManagedCredential):
            credential_ids.add(primary.id)
        credential_ids.update(
            binding.credential_id for binding in all_bindings if binding.service_id in service_ids
        )
        active_credentials: tuple[ManagedCredential, ...] = _active(
            await self._repository.credentials(organisation_id)
        )
        related_credentials: tuple[ManagedCredential, ...] = tuple(
            credential for credential in active_credentials if credential.id in credential_ids
        )
        now = self._clock()
        resources: dict[tuple[type[object], str], InventoryResource] = {
            (type(primary), primary.id): primary,
        }
        for environment in environments:
            resources[(type(environment), environment.id)] = _archive(
                environment, environment.revision, now
            )
        for service in services:
            resources[(type(service), service.id)] = _archive(service, service.revision, now)
        for credential in related_credentials:
            resources[(type(credential), credential.id)] = _archive(
                credential, credential.revision, now
            )
        removed_bindings = tuple(
            binding for binding in all_bindings if binding.credential_id in credential_ids
        )
        await self._repository.archive_inventory(
            tuple(resources.values()),
            removed_bindings,
        )
        return primary

    async def list_connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return _active(await self._repository.connections(organisation_id))

    async def list_applications(self, organisation_id: str) -> tuple[Application, ...]:
        return _active(await self._repository.applications(organisation_id))

    async def list_environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return _active(await self._repository.environments(organisation_id))

    async def list_services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return _active(await self._repository.services(organisation_id))

    async def list_credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return _active(await self._repository.credentials(organisation_id))

    async def graph(
        self,
        organisation_id: str,
    ) -> tuple[
        tuple[ManagedCredential, ...],
        tuple[ConsumerService, ...],
        tuple[ConsumerBinding, ...],
    ]:
        credentials = _active(await self._repository.credentials(organisation_id))
        consumer_services = _active(await self._repository.services(organisation_id))
        credential_ids = {credential.id for credential in credentials}
        service_ids = {service.id for service in consumer_services}
        bindings = tuple(
            binding
            for binding in await self._repository.bindings(organisation_id)
            if binding.credential_id in credential_ids and binding.service_id in service_ids
        )
        return (
            credentials,
            consumer_services,
            bindings,
        )

    async def _validate_connection(self, connection: Connection) -> None:
        if connection.interface is ConnectionInterface.BROWSER:
            if not connection.allowed_resources or any(
                not _domain_pattern(value) for value in connection.allowed_resources
            ):
                raise ResourceConflictError("browser connection must declare allowed domains")
            if not connection.capabilities.intersection(_BROWSER_CAPABILITIES):
                raise ResourceConflictError("browser connection must declare a browser capability")

    async def _validate_connection_usage(self, connection: Connection) -> None:
        credentials = _active(await self._repository.credentials(connection.organisation_id))
        consumer_services = _active(await self._repository.services(connection.organisation_id))
        if ConnectionRole.SECRET_STORE in connection.roles and any(
            credential.secret_store_connection_id == connection.id
            and not _resource_covered(credential.secret_reference, connection.allowed_resources)
            for credential in credentials
        ):
            raise ResourceConflictError("connection scope no longer covers an active secret")
        if ConnectionRole.RUNTIME in connection.roles and any(
            service.runtime_connection_id == connection.id
            and not _resource_covered(service.runtime_resource, connection.allowed_resources)
            for service in consumer_services
        ):
            raise ResourceConflictError("connection scope no longer covers an active runtime")
        if (
            connection.interface is ConnectionInterface.BROWSER
            and connection.playbook_id is not None
        ):
            assert connection.playbook_version_id is not None
            version = await self._repository.get_playbook_version(
                connection.organisation_id,
                connection.playbook_id,
                connection.playbook_version_id,
            )
            if any(
                not _domain_covered(domain, connection.allowed_resources)
                for domain in version.definition.allowed_domains
            ):
                raise ResourceConflictError("connection domains no longer cover its playbook")


def _tenant(actual: str, expected: str) -> None:
    if actual != expected:
        raise ResourceConflictError("inventory relationship crosses organisation boundaries")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResourceConflictError(f"Secret Manager returned no {label}")
    return value


def _domain_pattern(value: str) -> bool:
    return bool(value) and "." in value and all(ch.isalnum() or ch in ".-*" for ch in value)


def _resource_covered(resource: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in boundaries
    )


def _domain_covered(domain: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        domain == boundary
        or (boundary.startswith("*.") and domain.endswith(boundary.removeprefix("*")))
        for boundary in boundaries
    )


def _editable[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    value: T, expected_revision: int, label: str
) -> None:
    if value.archived_at is not None:
        raise ResourceConflictError(f"archived {label}s cannot be edited")
    if value.revision != expected_revision:
        raise ResourceConflictError(
            f"{label} expected revision {expected_revision}, found {value.revision}"
        )


def _change[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    value: T,
    expected_revision: int,
    updated_at: datetime,
    **updates: object | None,
) -> T:
    _editable(value, expected_revision, value.__class__.__name__.lower())
    populated = {key: item for key, item in updates.items() if item is not None}
    if not populated:
        raise ResourceConflictError("edit does not change any fields")
    changed = value.model_copy(
        update={**populated, "updated_at": updated_at, "revision": expected_revision + 1}
    )
    return value.__class__.model_validate(changed.model_dump())


def _archive[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    value: T,
    expected_revision: int,
    archived_at: datetime,
    **updates: object,
) -> T:
    _editable(value, expected_revision, value.__class__.__name__.lower())
    changed = value.model_copy(
        update={
            **updates,
            "archived_at": archived_at,
            "updated_at": archived_at,
            "revision": expected_revision + 1,
        }
    )
    return value.__class__.model_validate(changed.model_dump())


def _active[T: (Connection, Application, Environment, ConsumerService, ManagedCredential)](
    values: tuple[T, ...],
) -> tuple[T, ...]:
    return tuple(value for value in values if value.archived_at is None)
