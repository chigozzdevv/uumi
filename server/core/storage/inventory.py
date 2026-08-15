from typing import Any, TypeVar

from contracts import (
    Application,
    Connection,
    ConsumerBinding,
    ConsumerService,
    Contract,
    CredentialGeneration,
    Environment,
    ManagedCredential,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from pydantic import TypeAdapter

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.catalog import FirestoreCatalog
from core.storage.codec import encode
from core.storage.paths import FirestorePaths

T = TypeVar("T", bound=Contract)


class FirestoreInventoryRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._catalog = FirestoreCatalog(client)

    async def add_connection(self, value: Connection) -> Connection:
        await self._catalog.create(
            FirestorePaths.connection(value.organisation_id, value.id), value
        )
        return value

    async def add_application(self, value: Application) -> Application:
        await self._catalog.create(
            FirestorePaths.application(value.organisation_id, value.id), value
        )
        return value

    async def add_environment(self, value: Environment) -> Environment:
        await self._catalog.create(
            FirestorePaths.environment(value.organisation_id, value.id), value
        )
        return value

    async def add_service(self, value: ConsumerService) -> ConsumerService:
        await self._catalog.create(FirestorePaths.service(value.organisation_id, value.id), value)
        return value

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return await self._catalog.get(
            FirestorePaths.application(organisation_id, resource_id), Application
        )

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return await self._catalog.get(
            FirestorePaths.environment(organisation_id, resource_id), Environment
        )

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return await self._catalog.get(
            FirestorePaths.connection(organisation_id, resource_id), Connection
        )

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
    ) -> ManagedCredential:
        credential_ref = self._client.document(
            FirestorePaths.credential(credential.organisation_id, credential.id)
        )
        generation_ref = self._client.document(
            FirestorePaths.generation(generation.organisation_id, generation.id)
        )
        binding_refs = tuple(
            self._client.document(FirestorePaths.binding(binding.organisation_id, binding.id))
            for binding in bindings
        )
        service_refs = tuple(
            self._client.document(
                FirestorePaths.service(binding.organisation_id, binding.service_id)
            )
            for binding in bindings
        )
        connection_ref = self._client.document(
            FirestorePaths.connection(credential.organisation_id, credential.connection_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> ManagedCredential:
            credential_snapshot = await credential_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            connection_snapshot = await connection_ref.get(transaction=transaction)
            binding_snapshots = [
                await reference.get(transaction=transaction) for reference in binding_refs
            ]
            service_snapshots = [
                await reference.get(transaction=transaction) for reference in service_refs
            ]
            if credential_snapshot.exists or generation_snapshot.exists:
                raise ResourceConflictError(f"credential {credential.id} is already imported")
            if any(snapshot.exists for snapshot in binding_snapshots):
                raise ResourceConflictError("one or more credential bindings already exist")
            if not connection_snapshot.exists or any(
                not snapshot.exists for snapshot in service_snapshots
            ):
                raise ResourceNotFoundError("credential connection or consumer service is missing")
            transaction.create(credential_ref, encode(credential))
            transaction.create(generation_ref, encode(generation))
            for reference, binding in zip(binding_refs, bindings, strict=True):
                transaction.create(reference, encode(binding))
            return credential

        return await apply(self._client.transaction(max_attempts=5))

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return await self._list(f"organisations/{organisation_id}/credentials", ManagedCredential)

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return await self._list(f"organisations/{organisation_id}/connections", Connection)

    async def applications(self, organisation_id: str) -> tuple[Application, ...]:
        return await self._list(f"organisations/{organisation_id}/applications", Application)

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return await self._list(f"organisations/{organisation_id}/environments", Environment)

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return await self._list(f"organisations/{organisation_id}/services", ConsumerService)

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return await self._list(f"organisations/{organisation_id}/bindings", ConsumerBinding)

    async def _list(self, path: str, model: type[T]) -> tuple[T, ...]:
        adapter = TypeAdapter(model)
        values: list[T] = []
        async for snapshot in self._client.collection(path).limit(1000).stream():
            data: dict[str, Any] | None = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"inventory document {snapshot.id} has no data")
            values.append(adapter.validate_python(data))
        return tuple(values)
