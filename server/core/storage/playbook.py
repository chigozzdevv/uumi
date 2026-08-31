from datetime import datetime
from typing import Any

from contracts import Playbook, PlaybookDraft, PlaybookState, PlaybookVersion
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.errors import PlaybookError, ResourceConflictError, ResourceNotFoundError
from core.storage.catalog import FirestoreCatalog
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestorePlaybookRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._catalog = FirestoreCatalog(client)

    async def get(self, organisation_id: str, playbook_id: str) -> Playbook:
        return await self._catalog.get(
            FirestorePaths.playbook(organisation_id, playbook_id), Playbook
        )

    async def replace(self, value: Playbook, expected_revision: int) -> Playbook:
        def change(current: Playbook) -> Playbook:
            if (
                current.id != value.id
                or current.organisation_id != value.organisation_id
                or current.created_at != value.created_at
            ):
                raise PlaybookError("playbook replacement changed immutable identity")
            return value

        return await self._catalog.replace(
            FirestorePaths.playbook(value.organisation_id, value.id),
            Playbook,
            expected_revision,
            change,
        )

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
                if root.archived_at is not None:
                    raise PlaybookError("archived playbooks cannot receive new versions")
                if root.platform != definition.platform or root.name != definition.name:
                    raise PlaybookError("playbook identity cannot change across versions")
                number = root.latest_version + 1
                changed = root.model_copy(
                    update={
                        "latest_version": number,
                        "latest_version_id": version_id,
                        "updated_at": created_at,
                        "revision": root.revision + 1,
                    }
                )
            else:
                number = 1
                changed = Playbook(
                    id=playbook_id,
                    organisation_id=organisation_id,
                    name=definition.name,
                    platform=definition.platform,
                    latest_version=number,
                    latest_version_id=version_id,
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
                state=PlaybookState.DRAFT,
                source_ids=source_ids,
                created_by=actor_id,
                created_at=created_at,
            )
            transaction.set(playbook_ref, encode(changed))
            transaction.create(version_ref, encode(version))
            return changed, version

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

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> PlaybookVersion:
        root_ref = self._client.document(FirestorePaths.playbook(organisation_id, playbook_id))
        version_ref = self._client.document(
            FirestorePaths.playbook_version(organisation_id, playbook_id, version_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PlaybookVersion:
            root_snapshot = await root_ref.get(transaction=transaction)
            version_snapshot = await version_ref.get(transaction=transaction)
            if not root_snapshot.exists or not version_snapshot.exists:
                raise ResourceNotFoundError("playbook publication inputs are incomplete")
            root = Playbook.model_validate(_data(root_snapshot))
            version = PlaybookVersion.model_validate(_data(version_snapshot))
            if root.archived_at is not None:
                raise PlaybookError("archived playbooks cannot publish versions")
            if version.state is PlaybookState.PUBLISHED:
                return version
            if version.state is not PlaybookState.DRAFT:
                raise PlaybookError(f"playbook version cannot publish from {version.state}")
            published = version.model_copy(
                update={
                    "state": PlaybookState.PUBLISHED,
                    "published_by": actor_id,
                    "published_at": published_at,
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
                    raise PlaybookError("published playbook version is missing")
                previous = _data(previous_snapshot)
                if (
                    previous.get("id") != root.active_version_id
                    or previous.get("organisation_id") != organisation_id
                    or previous.get("playbook_id") != playbook_id
                ):
                    raise PlaybookError("published playbook version identity is invalid")
                transaction.update(
                    previous_ref,
                    {"state": PlaybookState.SUPERSEDED.value},
                )
            changed_root = root.model_copy(
                update={
                    "active_version_id": version.id,
                    "updated_at": published_at,
                    "revision": root.revision + 1,
                }
            )
            transaction.set(version_ref, encode(published))
            transaction.set(root_ref, encode(changed_root))
            return published

        return await apply(self._client.transaction(max_attempts=5))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise PlaybookError(f"playbook document {snapshot.id} has no data")
    return data
