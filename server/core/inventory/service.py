from datetime import datetime
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
    CredentialGeneration,
    Environment,
    ManagedCredential,
)

from core.errors import ResourceConflictError

_BROWSER_CAPABILITIES = frozenset({"browser.execute", "browser.authenticate"})


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
    ) -> ManagedCredential: ...

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]: ...

    async def applications(self, organisation_id: str) -> tuple[Application, ...]: ...

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]: ...

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]: ...

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]: ...


class InventoryService:
    def __init__(self, repository: InventoryRepository) -> None:
        self._repository = repository

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
    ) -> ManagedCredential:
        management = await self._repository.get_connection(
            credential.organisation_id,
            credential.connection_id,
        )
        if (
            ConnectionRole.PROVIDER not in management.roles
            or management.platform != credential.provider
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
            ):
                raise ResourceConflictError("credential binding requires an API runtime connection")
            if binding.secret_reference != generation.secret_reference:
                raise ResourceConflictError("credential binding must use the imported generation")
        return await self._repository.import_credential(credential, generation, bindings)

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return await self._repository.get_connection(organisation_id, resource_id)

    async def list_connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return await self._repository.connections(organisation_id)

    async def list_applications(self, organisation_id: str) -> tuple[Application, ...]:
        return await self._repository.applications(organisation_id)

    async def list_environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return await self._repository.environments(organisation_id)

    async def graph(
        self,
        organisation_id: str,
    ) -> tuple[
        tuple[ManagedCredential, ...],
        tuple[ConsumerService, ...],
        tuple[ConsumerBinding, ...],
    ]:
        return (
            await self._repository.credentials(organisation_id),
            await self._repository.services(organisation_id),
            await self._repository.bindings(organisation_id),
        )


def _tenant(actual: str, expected: str) -> None:
    if actual != expected:
        raise ResourceConflictError("inventory relationship crosses organisation boundaries")


def _domain_pattern(value: str) -> bool:
    return bool(value) and "." in value and all(ch.isalnum() or ch in ".-*" for ch in value)


def _resource_covered(resource: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in boundaries
    )
