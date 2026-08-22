from datetime import datetime
from typing import Any, TypeVar

from contracts import (
    Application,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    Contract,
    ControlVersion,
    CredentialGeneration,
    Environment,
    ManagedCredential,
    PlaybookVersion,
    ProbeState,
    ProbeVersion,
    SetupSession,
    SetupStatus,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from policy import digest
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

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]:
        references = (
            self._client.document(
                FirestorePaths.application(application.organisation_id, application.id)
            ),
            self._client.document(
                FirestorePaths.environment(environment.organisation_id, environment.id)
            ),
            self._client.document(FirestorePaths.service(service.organisation_id, service.id)),
        )

        @async_transactional
        async def apply(
            transaction: AsyncTransaction,
        ) -> tuple[Application, Environment, ConsumerService]:
            snapshots = [await reference.get(transaction=transaction) for reference in references]
            if any(snapshot.exists for snapshot in snapshots):
                raise ResourceConflictError("application setup resource already exists")
            for reference, value in zip(
                references,
                (application, environment, service),
                strict=True,
            ):
                transaction.create(reference, encode(value))
            return application, environment, service

        return await apply(self._client.transaction(max_attempts=5))

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return await self._catalog.get(
            FirestorePaths.application(organisation_id, resource_id), Application
        )

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return await self._catalog.get(
            FirestorePaths.environment(organisation_id, resource_id), Environment
        )

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        return await self._catalog.get(
            FirestorePaths.service(organisation_id, resource_id), ConsumerService
        )

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return await self._catalog.get(
            FirestorePaths.connection(organisation_id, resource_id), Connection
        )

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        return await self._catalog.get(
            FirestorePaths.credential(organisation_id, resource_id), ManagedCredential
        )

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        return await self._catalog.get(
            FirestorePaths.control_version(organisation_id, credential_id, version_id),
            ControlVersion,
        )

    async def get_playbook_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion:
        return await self._catalog.get(
            FirestorePaths.playbook_version(organisation_id, playbook_id, version_id),
            PlaybookVersion,
        )

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection:
        return await self._replace(
            FirestorePaths.connection(value.organisation_id, value.id),
            Connection,
            value,
            expected_revision,
        )

    async def replace_application(self, value: Application, expected_revision: int) -> Application:
        return await self._replace(
            FirestorePaths.application(value.organisation_id, value.id),
            Application,
            value,
            expected_revision,
        )

    async def replace_environment(self, value: Environment, expected_revision: int) -> Environment:
        return await self._replace(
            FirestorePaths.environment(value.organisation_id, value.id),
            Environment,
            value,
            expected_revision,
        )

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService:
        return await self._replace(
            FirestorePaths.service(value.organisation_id, value.id),
            ConsumerService,
            value,
            expected_revision,
        )

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential:
        return await self._replace(
            FirestorePaths.credential(value.organisation_id, value.id),
            ManagedCredential,
            value,
            expected_revision,
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
                    "authorization_reference": authorization_reference,
                    "status": status,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection:
        reference = self._client.document(FirestorePaths.connection(organisation_id, connection_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Connection:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"connection {connection_id} was not found")
            current = Connection.model_validate(_snapshot_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"connection expected revision {expected_revision}, found {current.revision}"
                )
            status = (
                ConnectionStatus.READY
                if current.authorization_reference is not None
                else ConnectionStatus.SETUP_REQUIRED
            )
            changed = current.model_copy(
                update={
                    "playbook_id": playbook_id,
                    "playbook_version_id": version_id,
                    "status": status,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def detach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        updated_at: datetime,
    ) -> Connection:
        reference = self._client.document(FirestorePaths.connection(organisation_id, connection_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Connection:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"connection {connection_id} was not found")
            current = Connection.model_validate(_snapshot_data(snapshot))
            if current.revision != expected_revision or current.playbook_id is None:
                raise ResourceConflictError("connection playbook changed before archive")
            changed = current.model_copy(
                update={
                    "playbook_id": None,
                    "playbook_version_id": None,
                    "status": ConnectionStatus.SETUP_REQUIRED,
                    "updated_at": updated_at,
                    "revision": expected_revision + 1,
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
            or changed_session.auth_reference != changed_connection.authorization_reference
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
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
    ) -> ManagedCredential:
        credential_ref = self._client.document(
            FirestorePaths.credential(credential.organisation_id, credential.id)
        )
        generation_ref = self._client.document(
            FirestorePaths.generation(generation.organisation_id, generation.id)
        )
        controls_ref = self._client.document(
            FirestorePaths.control_version(
                controls.organisation_id, controls.credential_id, controls.id
            )
        )
        probe_refs = tuple(
            self._client.document(FirestorePaths.probe_version(probe.organisation_id, probe.id))
            for probe in probes
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
        runtime_refs = tuple(
            self._client.document(
                FirestorePaths.connection(binding.organisation_id, binding.runtime_connection_id)
            )
            for binding in bindings
        )
        connection_ref = self._client.document(
            FirestorePaths.connection(credential.organisation_id, credential.connection_id)
        )
        secret_connection_ref = self._client.document(
            FirestorePaths.connection(
                credential.organisation_id, credential.secret_store_connection_id
            )
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> ManagedCredential:
            credential_snapshot = await credential_ref.get(transaction=transaction)
            generation_snapshot = await generation_ref.get(transaction=transaction)
            controls_snapshot = await controls_ref.get(transaction=transaction)
            probe_snapshots = [
                await reference.get(transaction=transaction) for reference in probe_refs
            ]
            connection_snapshot = await connection_ref.get(transaction=transaction)
            secret_connection_snapshot = await secret_connection_ref.get(transaction=transaction)
            binding_snapshots = [
                await reference.get(transaction=transaction) for reference in binding_refs
            ]
            service_snapshots = [
                await reference.get(transaction=transaction) for reference in service_refs
            ]
            runtime_snapshots = [
                await reference.get(transaction=transaction) for reference in runtime_refs
            ]
            if credential_snapshot.exists or generation_snapshot.exists or controls_snapshot.exists:
                raise ResourceConflictError(f"credential {credential.id} is already imported")
            if any(snapshot.exists for snapshot in probe_snapshots):
                raise ResourceConflictError("one or more control probe versions already exist")
            if any(snapshot.exists for snapshot in binding_snapshots):
                raise ResourceConflictError("one or more credential bindings already exist")
            if (
                not connection_snapshot.exists
                or not secret_connection_snapshot.exists
                or any(not snapshot.exists for snapshot in service_snapshots)
                or any(not snapshot.exists for snapshot in runtime_snapshots)
            ):
                raise ResourceNotFoundError("credential connection or consumer service is missing")
            management = Connection.model_validate(_snapshot_data(connection_snapshot))
            secret_store = Connection.model_validate(_snapshot_data(secret_connection_snapshot))
            services = tuple(
                ConsumerService.model_validate(_snapshot_data(snapshot))
                for snapshot in service_snapshots
            )
            runtimes = tuple(
                Connection.model_validate(_snapshot_data(snapshot))
                for snapshot in runtime_snapshots
            )
            if (
                management.organisation_id != credential.organisation_id
                or ConnectionRole.PROVIDER not in management.roles
                or management.platform != credential.provider
                or management.archived_at is not None
                or management.status is not ConnectionStatus.READY
                or (
                    management.interface is ConnectionInterface.BROWSER
                    and (management.playbook_id is None or management.playbook_version_id is None)
                )
            ):
                raise ResourceConflictError("credential provider connection changed during import")
            if (
                secret_store.organisation_id != credential.organisation_id
                or ConnectionRole.SECRET_STORE not in secret_store.roles
                or secret_store.interface is not ConnectionInterface.API
                or secret_store.archived_at is not None
                or secret_store.status is not ConnectionStatus.READY
                or not _covered(credential.secret_resource, secret_store.allowed_resources)
            ):
                raise ResourceConflictError(
                    "credential secret-store connection changed during import"
                )
            if (
                generation.organisation_id != credential.organisation_id
                or generation.credential_id != credential.id
                or credential.active_generation_id != generation.id
                or credential.secret_reference != generation.secret_reference
                or controls.organisation_id != credential.organisation_id
                or controls.credential_id != credential.id
                or controls.id != credential.control_version
                or controls.number != 1
                or controls.digest != digest(controls.definition)
            ):
                raise ResourceConflictError("credential import lineage is inconsistent")
            if any(
                probe.organisation_id != credential.organisation_id
                or probe.id
                not in {
                    probe_id
                    for ids in controls.definition.probe_versions.values()
                    for probe_id in ids
                }
                or probe.state is not ProbeState.ACTIVE
                or probe.digest != digest(probe.definition)
                for probe in probes
            ):
                raise ResourceConflictError("credential control probes are inconsistent")
            if len(bindings) != len(credential.consumer_ids) or set(credential.consumer_ids) != {
                binding.service_id for binding in bindings
            }:
                raise ResourceConflictError("credential consumers changed during import")
            for binding, service, runtime in zip(bindings, services, runtimes, strict=True):
                if (
                    binding.organisation_id != credential.organisation_id
                    or binding.credential_id != credential.id
                    or binding.current_generation_id != generation.id
                    or binding.secret_reference != generation.secret_reference
                    or binding.service_id != service.id
                    or binding.environment_id != service.environment_id
                    or binding.runtime_connection_id != service.runtime_connection_id
                    or binding.runtime_resource != service.runtime_resource
                    or runtime.id != binding.runtime_connection_id
                    or ConnectionRole.RUNTIME not in runtime.roles
                    or runtime.interface is not ConnectionInterface.API
                    or runtime.archived_at is not None
                    or runtime.status is not ConnectionStatus.READY
                ):
                    raise ResourceConflictError("credential consumer binding changed during import")
            transaction.create(credential_ref, encode(credential))
            transaction.create(generation_ref, encode(generation))
            transaction.create(controls_ref, encode(controls))
            for reference, probe in zip(probe_refs, probes, strict=True):
                transaction.create(reference, encode(probe))
            for reference, binding in zip(binding_refs, bindings, strict=True):
                transaction.create(reference, encode(binding))
            return credential

        return await apply(self._client.transaction(max_attempts=5))

    async def replace_controls(
        self,
        credential: ManagedCredential,
        expected_revision: int,
        controls: ControlVersion,
    ) -> tuple[ManagedCredential, ControlVersion]:
        credential_ref = self._client.document(
            FirestorePaths.credential(credential.organisation_id, credential.id)
        )
        controls_ref = self._client.document(
            FirestorePaths.control_version(
                controls.organisation_id, controls.credential_id, controls.id
            )
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[ManagedCredential, ControlVersion]:
            credential_snapshot = await credential_ref.get(transaction=transaction)
            controls_snapshot = await controls_ref.get(transaction=transaction)
            if not credential_snapshot.exists:
                raise ResourceNotFoundError(f"credential {credential.id} was not found")
            if controls_snapshot.exists:
                raise ResourceConflictError(f"control version {controls.id} already exists")
            current = ManagedCredential.model_validate(_snapshot_data(credential_snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"credential expected revision {expected_revision}, found {current.revision}"
                )
            if (
                credential.revision != expected_revision + 1
                or credential.control_version != controls.id
                or controls.credential_id != credential.id
                or controls.organisation_id != credential.organisation_id
            ):
                raise StorageIntegrityError("credential controls update is inconsistent")
            transaction.create(controls_ref, encode(controls))
            transaction.set(credential_ref, encode(credential))
            return credential, controls

        return await apply(self._client.transaction(max_attempts=5))

    async def archive_inventory(
        self,
        resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ],
        bindings: tuple[ConsumerBinding, ...],
    ) -> None:
        references = tuple(
            self._client.document(_inventory_path(resource)) for resource in resources
        )
        binding_references = tuple(
            self._client.document(FirestorePaths.binding(binding.organisation_id, binding.id))
            for binding in bindings
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshots = [await reference.get(transaction=transaction) for reference in references]
            binding_snapshots = [
                await reference.get(transaction=transaction) for reference in binding_references
            ]
            if any(not snapshot.exists for snapshot in snapshots):
                raise ResourceNotFoundError("archive target changed before confirmation")
            if any(not snapshot.exists for snapshot in binding_snapshots):
                raise ResourceConflictError("archive dependencies changed before confirmation")
            for snapshot, resource in zip(snapshots, resources, strict=True):
                current = type(resource).model_validate(_snapshot_data(snapshot))
                if (
                    current.organisation_id != resource.organisation_id
                    or current.id != resource.id
                    or current.revision + 1 != resource.revision
                    or current.archived_at is not None
                    or resource.archived_at is None
                ):
                    raise ResourceConflictError("archive target changed before confirmation")
            for snapshot, binding in zip(binding_snapshots, bindings, strict=True):
                if ConsumerBinding.model_validate(_snapshot_data(snapshot)) != binding:
                    raise ResourceConflictError("archive dependencies changed before confirmation")
            for reference, resource in zip(references, resources, strict=True):
                transaction.set(reference, encode(resource))
            for reference in binding_references:
                transaction.delete(reference)

        await apply(self._client.transaction(max_attempts=5))

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return await self._list(f"organisations/{organisation_id}/credentials", ManagedCredential)

    async def count_credentials(self, organisation_id: str) -> int:
        return sum(
            credential.archived_at is None for credential in await self.credentials(organisation_id)
        )

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

    async def _replace(
        self,
        path: str,
        model: type[T],
        value: T,
        expected_revision: int,
    ) -> T:
        def change(current: T) -> T:
            immutable = {"id", "organisation_id", "created_at"}
            if current.model_dump(include=immutable) != value.model_dump(include=immutable):
                raise StorageIntegrityError("inventory replacement changed immutable identity")
            return value

        return await self._catalog.replace(path, model, expected_revision, change)


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    data: dict[str, Any] | None = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"inventory document {snapshot.id} has no data")
    return data


def _inventory_path(
    resource: Connection | Application | Environment | ConsumerService | ManagedCredential,
) -> str:
    if isinstance(resource, Connection):
        return FirestorePaths.connection(resource.organisation_id, resource.id)
    if isinstance(resource, Application):
        return FirestorePaths.application(resource.organisation_id, resource.id)
    if isinstance(resource, Environment):
        return FirestorePaths.environment(resource.organisation_id, resource.id)
    if isinstance(resource, ConsumerService):
        return FirestorePaths.service(resource.organisation_id, resource.id)
    return FirestorePaths.credential(resource.organisation_id, resource.id)


def _covered(resource: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in boundaries
    )
