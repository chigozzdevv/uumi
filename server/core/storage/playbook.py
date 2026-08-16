from datetime import datetime
from typing import Any

from contracts import (
    Connection,
    ConsumerBinding,
    DryRun,
    DryRunStatus,
    Environment,
    ManagedCredential,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookState,
    PlaybookVersion,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from pydantic import TypeAdapter

from core.errors import PlaybookError, ResourceConflictError, ResourceNotFoundError
from core.playbook.service import validate_assignment_connections
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestorePlaybookRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        path = f"{FirestorePaths.organisation(organisation_id)}/playbooks"
        playbooks: list[Playbook] = []
        async for snapshot in self._client.collection(path).limit(limit).stream():
            playbooks.append(Playbook.model_validate(_data(snapshot)))
        return tuple(playbooks)

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
        playbook_ref = self._client.document(FirestorePaths.playbook(organisation_id, playbook_id))
        version_ref = self._client.document(
            FirestorePaths.playbook_version(organisation_id, playbook_id, version_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[Playbook, PlaybookVersion]:
            root_snapshot = await playbook_ref.get(transaction=transaction)
            existing_version = await version_ref.get(transaction=transaction)
            if existing_version.exists:
                raise ResourceConflictError(f"playbook version {version_id} already exists")
            if root_snapshot.exists:
                root = Playbook.model_validate(_data(root_snapshot))
                if root.provider != definition.provider or root.name != definition.name:
                    raise PlaybookError("playbook identity cannot change across versions")
                number = root.latest_version + 1
                updated = root.model_copy(
                    update={
                        "latest_version": number,
                        "updated_at": created_at,
                        "revision": root.revision + 1,
                    }
                )
            else:
                number = 1
                updated = Playbook(
                    id=playbook_id,
                    organisation_id=organisation_id,
                    name=definition.name,
                    provider=definition.provider,
                    latest_version=number,
                    created_at=created_at,
                    updated_at=created_at,
                )
            version = PlaybookVersion(
                id=version_id,
                organisation_id=organisation_id,
                playbook_id=playbook_id,
                number=number,
                definition=definition,
                digest=definition_digest,
                state=PlaybookState.TEST,
                source_ids=source_ids,
                created_by=actor_id,
                created_at=created_at,
            )
            transaction.set(playbook_ref, encode(updated))
            transaction.create(version_ref, encode(version))
            return updated, version

        return await apply(self._client.transaction(max_attempts=5))

    async def get_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion:
        snapshot = await self._client.document(
            FirestorePaths.playbook_version(organisation_id, playbook_id, version_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"playbook version {version_id} was not found")
        return PlaybookVersion.model_validate(_data(snapshot))

    async def get_dryrun(
        self, organisation_id: str, playbook_id: str, dryrun_id: str
    ) -> DryRun | None:
        snapshot = await self._client.document(
            FirestorePaths.dryrun(organisation_id, playbook_id, dryrun_id)
        ).get()
        if not snapshot.exists:
            return None
        return DryRun.model_validate(_data(snapshot))

    async def validate_dryrun(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        environment_id: str,
        credential_id: str,
    ) -> None:
        version = await self.get_version(organisation_id, playbook_id, version_id)
        environment = await self._catalog_value(
            FirestorePaths.environment(organisation_id, environment_id), Environment
        )
        credential = await self._catalog_value(
            FirestorePaths.credential(organisation_id, credential_id), ManagedCredential
        )
        assignment = await self._catalog_value(
            FirestorePaths.assignment(organisation_id, credential_id), PlaybookAssignment
        )
        if version.state is not PlaybookState.TEST:
            raise PlaybookError("only a test-required playbook version can start a dry run")
        if environment.production:
            raise PlaybookError("playbook dry runs cannot execute in production")
        if (
            not assignment.dry_run_only
            or assignment.environment_id != environment.id
            or assignment.playbook_id != playbook_id
            or assignment.version_id != version.id
        ):
            raise PlaybookError("dry run is not bound to its isolated playbook assignment")
        bindings: list[ConsumerBinding] = []
        path = f"{FirestorePaths.organisation(organisation_id)}/bindings"
        async for snapshot in (
            self._client.collection(path).where("credential_id", "==", credential_id).stream()
        ):
            bindings.append(ConsumerBinding.model_validate(_data(snapshot)))
        if (
            not bindings
            or {item.service_id for item in bindings} != set(credential.consumer_ids)
            or any(item.environment_id != environment.id for item in bindings)
        ):
            raise PlaybookError("dry-run consumers are not isolated in the declared environment")

    async def activate(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        dryrun_id: str,
        actor_id: str,
        activated_at: datetime,
    ) -> PlaybookVersion:
        root_ref = self._client.document(FirestorePaths.playbook(organisation_id, playbook_id))
        version_ref = self._client.document(
            FirestorePaths.playbook_version(organisation_id, playbook_id, version_id)
        )
        dryrun_ref = self._client.document(
            FirestorePaths.dryrun(organisation_id, playbook_id, dryrun_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PlaybookVersion:
            root_snapshot = await root_ref.get(transaction=transaction)
            version_snapshot = await version_ref.get(transaction=transaction)
            dryrun_snapshot = await dryrun_ref.get(transaction=transaction)
            if (
                not root_snapshot.exists
                or not version_snapshot.exists
                or not dryrun_snapshot.exists
            ):
                raise ResourceNotFoundError("playbook activation inputs are incomplete")
            root = Playbook.model_validate(_data(root_snapshot))
            version = PlaybookVersion.model_validate(_data(version_snapshot))
            dryrun = DryRun.model_validate(_data(dryrun_snapshot))
            if dryrun.version_id != version.id or dryrun.status is not DryRunStatus.PASSED:
                raise PlaybookError("playbook activation requires its own passed dry run")
            if version.state not in {PlaybookState.APPROVAL, PlaybookState.ACTIVE}:
                raise PlaybookError(f"playbook version cannot activate from {version.state}")
            if version.state is PlaybookState.ACTIVE:
                return version

            active = version.model_copy(
                update={
                    "state": PlaybookState.ACTIVE,
                    "dry_run_id": dryrun.id,
                    "approved_by": actor_id,
                    "approved_at": activated_at,
                }
            )
            if root.active_version_id is not None and root.active_version_id != version.id:
                previous_ref = self._client.document(
                    FirestorePaths.playbook_version(
                        organisation_id,
                        playbook_id,
                        root.active_version_id,
                    )
                )
                previous_snapshot = await previous_ref.get(transaction=transaction)
                if not previous_snapshot.exists:
                    raise PlaybookError("active playbook version is missing")
                previous = PlaybookVersion.model_validate(_data(previous_snapshot))
                transaction.set(
                    previous_ref,
                    encode(previous.model_copy(update={"state": PlaybookState.SUPERSEDED})),
                )
            changed_root = root.model_copy(
                update={
                    "active_version_id": version.id,
                    "updated_at": activated_at,
                    "revision": root.revision + 1,
                }
            )
            transaction.set(version_ref, encode(active))
            transaction.set(root_ref, encode(changed_root))
            return active

        return await apply(self._client.transaction(max_attempts=5))

    async def get_assignment(
        self, organisation_id: str, credential_id: str
    ) -> PlaybookAssignment | None:
        snapshot = await self._client.document(
            FirestorePaths.assignment(organisation_id, credential_id)
        ).get()
        if not snapshot.exists:
            return None
        return PlaybookAssignment.model_validate(_data(snapshot))

    async def assign(self, assignment: PlaybookAssignment) -> PlaybookAssignment:
        version_ref = self._client.document(
            FirestorePaths.playbook_version(
                assignment.organisation_id,
                assignment.playbook_id,
                assignment.version_id,
            )
        )
        assignment_ref = self._client.document(
            FirestorePaths.assignment(assignment.organisation_id, assignment.credential_id)
        )
        lock_ref = self._client.document(
            FirestorePaths.lock(assignment.organisation_id, assignment.credential_id)
        )
        if assignment.dry_run_only:
            environment = await self._catalog_value(
                FirestorePaths.environment(
                    assignment.organisation_id,
                    _required(assignment.environment_id, "dry-run environment"),
                ),
                Environment,
            )
            if environment.production:
                raise PlaybookError("dry-run assignments cannot target production")
            credential = await self._catalog_value(
                FirestorePaths.credential(assignment.organisation_id, assignment.credential_id),
                ManagedCredential,
            )
            bindings: list[ConsumerBinding] = []
            path = f"{FirestorePaths.organisation(assignment.organisation_id)}/bindings"
            async for snapshot in (
                self._client.collection(path)
                .where("credential_id", "==", assignment.credential_id)
                .stream()
            ):
                bindings.append(ConsumerBinding.model_validate(_data(snapshot)))
            if (
                not bindings
                or {item.service_id for item in bindings} != set(credential.consumer_ids)
                or any(item.environment_id != environment.id for item in bindings)
            ):
                raise PlaybookError("dry-run consumers must share one non-production environment")

        connection_refs = [
            self._client.document(FirestorePaths.connection(assignment.organisation_id, item))
            for item in assignment.connection_ids
        ]

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PlaybookAssignment:
            version_snapshot = await version_ref.get(transaction=transaction)
            current = await assignment_ref.get(transaction=transaction)
            lock = await lock_ref.get(transaction=transaction)
            if not version_snapshot.exists:
                raise ResourceNotFoundError(
                    f"playbook version {assignment.version_id} was not found"
                )
            version = PlaybookVersion.model_validate(_data(version_snapshot))
            expected_state = PlaybookState.TEST if assignment.dry_run_only else PlaybookState.ACTIVE
            if version.state is not expected_state:
                raise PlaybookError(
                    f"assignment requires a playbook version in {expected_state.value}"
                )
            required = set(version.definition.required_connections)
            if not required.issubset(assignment.connection_ids):
                raise PlaybookError("assignment is missing required connections")
            loaded: list[Connection] = []
            for reference in connection_refs:
                snapshot = await reference.get(transaction=transaction)
                if not snapshot.exists:
                    raise PlaybookError(f"connection {reference.id} was not found")
                loaded.append(Connection.model_validate(_data(snapshot)))
            validate_assignment_connections(
                version.definition.execution,
                tuple(loaded),
                version.definition.allowed_domains,
            )
            if current.exists:
                stored = PlaybookAssignment.model_validate(_data(current))
                if stored == assignment:
                    return assignment
                if stored.dry_run_only != assignment.dry_run_only:
                    raise PlaybookError(
                        "dry-run and production assignments cannot replace each other"
                    )
                if lock.exists:
                    raise PlaybookError("an assignment cannot change during an active rotation")
                if assignment.dry_run_only and stored.environment_id != assignment.environment_id:
                    raise PlaybookError("dry-run assignment cannot change its isolated environment")
            transaction.set(assignment_ref, encode(assignment))
            return assignment

        return await apply(self._client.transaction(max_attempts=5))

    async def _catalog_value[T](self, path: str, model: type[T]) -> T:
        snapshot = await self._client.document(path).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"resource {snapshot.id} was not found")
        return TypeAdapter(model).validate_python(_data(snapshot))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise PlaybookError(f"playbook document {snapshot.id} has no data")
    return data


def _required(value: str | None, label: str) -> str:
    if value is None:
        raise PlaybookError(f"{label} is missing")
    return value
