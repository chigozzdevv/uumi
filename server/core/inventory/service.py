from typing import Protocol

from contracts import (
    Application,
    Connection,
    ConsumerBinding,
    ConsumerService,
    CredentialGeneration,
    Environment,
    ManagedCredential,
)

from core.errors import ResourceConflictError


class InventoryRepository(Protocol):
    async def add_connection(self, value: Connection) -> Connection: ...

    async def add_application(self, value: Application) -> Application: ...

    async def add_environment(self, value: Environment) -> Environment: ...

    async def add_service(self, value: ConsumerService) -> ConsumerService: ...

    async def get_application(self, organisation_id: str, resource_id: str) -> Application: ...

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment: ...

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

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
        await self._repository.get_connection(
            service.organisation_id,
            service.runtime_connection_id,
        )
        return await self._repository.add_service(service)

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
    ) -> ManagedCredential:
        if generation.credential_id != credential.id:
            raise ResourceConflictError("generation does not belong to the credential")
        if credential.active_generation_id != generation.id:
            raise ResourceConflictError("imported generation must be the active generation")
        if set(credential.consumer_ids) != {binding.service_id for binding in bindings}:
            raise ResourceConflictError("credential consumers and bindings must match exactly")
        if any(
            binding.credential_id != credential.id
            or binding.current_generation_id != generation.id
            or binding.organisation_id != credential.organisation_id
            for binding in bindings
        ):
            raise ResourceConflictError("credential binding lineage is inconsistent")
        return await self._repository.import_credential(credential, generation, bindings)

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
