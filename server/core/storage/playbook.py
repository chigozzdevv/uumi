from datetime import datetime
from typing import Any

from contracts import (
    DryRun,
    DryRunStatus,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookState,
    PlaybookVersion,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.errors import PlaybookError, ResourceConflictError, ResourceNotFoundError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestorePlaybookRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

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

    async def save_dryrun(self, result: DryRun) -> DryRun:
        version_ref = self._client.document(
            FirestorePaths.playbook_version(
                result.organisation_id,
                result.playbook_id,
                result.version_id,
            )
        )
        dryrun_ref = self._client.document(
            FirestorePaths.dryrun(result.organisation_id, result.playbook_id, result.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> DryRun:
            version_snapshot = await version_ref.get(transaction=transaction)
            existing = await dryrun_ref.get(transaction=transaction)
            if not version_snapshot.exists:
                raise ResourceNotFoundError(f"playbook version {result.version_id} was not found")
            if existing.exists:
                current = DryRun.model_validate(_data(existing))
                if current != result:
                    raise ResourceConflictError(f"dry run {result.id} already has another result")
                return current
            version = PlaybookVersion.model_validate(_data(version_snapshot))
            next_state = (
                PlaybookState.APPROVAL
                if result.status is DryRunStatus.PASSED
                else PlaybookState.TEST
            )
            changed = version.model_copy(update={"state": next_state, "dry_run_id": result.id})
            transaction.create(dryrun_ref, encode(result))
            transaction.set(version_ref, encode(changed))
            return result

        return await apply(self._client.transaction(max_attempts=5))

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

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PlaybookAssignment:
            version_snapshot = await version_ref.get(transaction=transaction)
            current = await assignment_ref.get(transaction=transaction)
            if not version_snapshot.exists:
                raise ResourceNotFoundError(
                    f"playbook version {assignment.version_id} was not found"
                )
            version = PlaybookVersion.model_validate(_data(version_snapshot))
            if version.state is not PlaybookState.ACTIVE:
                raise PlaybookError("only active playbook versions can be assigned")
            required = set(version.definition.required_connections)
            if not required.issubset(assignment.connection_ids):
                raise PlaybookError("assignment is missing required connections")
            if current.exists and PlaybookAssignment.model_validate(_data(current)) == assignment:
                return assignment
            transaction.set(assignment_ref, encode(assignment))
            return assignment

        return await apply(self._client.transaction(max_attempts=5))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise PlaybookError(f"playbook document {snapshot.id} has no data")
    return data
