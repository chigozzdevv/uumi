from datetime import datetime
from typing import Any, TypeVar

from contracts import (
    Application,
    Connection,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    Contract,
    CredentialGeneration,
    Environment,
    ManagedCredential,
    SetupSession,
    SetupStatus,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from pydantic import TypeAdapter

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.catalog import FirestoreCatalog, aggregate_count
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

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        auth_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        reference = self._client.document(FirestorePaths.connection(organisation_id, connection_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Connection:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"connection {connection_id} was not found")
            data = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"connection {connection_id} has no data")
            current = Connection.model_validate(data)
            if current.organisation_id != organisation_id:
                raise StorageIntegrityError(f"connection {connection_id} crosses tenant boundary")
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"connection expected revision {expected_revision}, found {current.revision}"
                )
            changed = current.model_copy(
                update={
                    "auth_reference": auth_reference,
                    "status": status,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def complete_setup(
        self,
        current_session: SetupSession,
        changed_session: SetupSession,
        current_connection: Connection,
        changed_connection: Connection,
    ) -> tuple[SetupSession, Connection]:
        session_ref = self._client.document(
            FirestorePaths.setup(current_session.organisation_id, current_session.id)
        )
        connection_ref = self._client.document(
            FirestorePaths.connection(current_connection.organisation_id, current_connection.id)
        )
        if (
            changed_session.status is not SetupStatus.COMPLETE
            or changed_session.auth_reference is None
            or changed_session.auth_reference != changed_connection.auth_reference
            or changed_connection.status is not ConnectionStatus.READY
            or changed_session.revision != current_session.revision + 1
            or changed_connection.revision != current_connection.revision + 1
            or changed_session.id != current_session.id
            or changed_connection.id != current_connection.id
            or changed_session.organisation_id != current_session.organisation_id
            or changed_connection.organisation_id != current_connection.organisation_id
            or current_session.connection_id != current_connection.id
        ):
            raise StorageIntegrityError("browser setup completion is inconsistent")

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[SetupSession, Connection]:
            session_snapshot = await session_ref.get(transaction=transaction)
            connection_snapshot = await connection_ref.get(transaction=transaction)
            if not session_snapshot.exists or not connection_snapshot.exists:
                raise ResourceNotFoundError("browser setup completion resources were not found")
            stored_session = SetupSession.model_validate(session_snapshot.to_dict())
            stored_connection = Connection.model_validate(connection_snapshot.to_dict())
            if stored_session == changed_session and stored_connection == changed_connection:
                return stored_session, stored_connection
            if stored_session != current_session or stored_connection != current_connection:
                raise ResourceConflictError("browser setup changed before atomic completion")
            transaction.set(session_ref, encode(changed_session))
            transaction.set(connection_ref, encode(changed_connection))
            return changed_session, changed_connection

        return await apply(self._client.transaction(max_attempts=5))

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

    async def count_credentials(self, organisation_id: str) -> int:
        query = self._client.collection(f"organisations/{organisation_id}/credentials")
        return await aggregate_count(query)

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

    async def generations(self, organisation_id: str) -> tuple[CredentialGeneration, ...]:
        return await self._list(
            f"organisations/{organisation_id}/generations", CredentialGeneration
        )

    async def record_observation(
        self,
        organisation_id: str,
        credential_id: str,
        generation_id: str,
        expected_revision: int,
        observed_at: datetime,
        expires_at: datetime | None,
        rotation_due_at: datetime | None,
        metadata_digest: str,
    ) -> ManagedCredential:
        credential_ref = self._client.document(
            FirestorePaths.credential(organisation_id, credential_id)
        )
        generation_ref = self._client.document(
            FirestorePaths.generation(organisation_id, generation_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> ManagedCredential:
            credential_snapshot = await credential_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            if not credential_snapshot.exists or not generation_snapshot.exists:
                raise ResourceNotFoundError("credential observation target is missing")
            credential = ManagedCredential.model_validate(_snapshot_data(credential_snapshot))
            generation = CredentialGeneration.model_validate(_snapshot_data(generation_snapshot))
            if credential.revision != expected_revision:
                raise ResourceConflictError(
                    f"credential expected revision {expected_revision}, found {credential.revision}"
                )
            if (
                credential.active_generation_id != generation.id
                or generation.credential_id != credential.id
            ):
                raise ResourceConflictError("credential observation generation changed")
            changed_generation = generation.model_copy(
                update={
                    "expires_at": expires_at,
                    "last_observed_at": observed_at,
                    "metadata_digest": metadata_digest,
                }
            )
            # Monitoring metadata is transaction-fenced but does not advance the operator-facing
            # inventory revision, so a polling schedule cannot manufacture mutation conflicts.
            changed_credential = credential.model_copy(
                update={
                    "expires_at": expires_at,
                    "rotation_due_at": rotation_due_at,
                    "last_observed_at": observed_at,
                    "metadata_digest": metadata_digest,
                    "updated_at": observed_at,
                }
            )
            transaction.set(generation_ref, encode(changed_generation))
            transaction.set(credential_ref, encode(changed_credential))
            return changed_credential

        return await apply(self._client.transaction(max_attempts=5))

    async def _list(self, path: str, model: type[T]) -> tuple[T, ...]:
        adapter = TypeAdapter(model)
        values: list[T] = []
        async for snapshot in self._client.collection(path).limit(1000).stream():
            data: dict[str, Any] | None = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"inventory document {snapshot.id} has no data")
            values.append(adapter.validate_python(data))
        return tuple(values)


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    data: dict[str, Any] | None = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"inventory document {snapshot.id} has no data")
    return data
