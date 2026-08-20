from contracts import WalkthroughSource
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreWalkthroughRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]:
        reference = self._client.document(
            FirestorePaths.walkthrough(
                source.organisation_id,
                source.playbook_id,
                source.id,
            )
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[WalkthroughSource, bool]:
            snapshot = await reference.get(transaction=transaction)
            if snapshot.exists:
                current = _source(snapshot.to_dict())
                if _binding(current) != _binding(source):
                    raise ResourceConflictError("walkthrough ID is bound to another upload")
                return current, False
            transaction.create(reference, encode(source))
            return source, True

        return await apply(self._client.transaction(max_attempts=5))

    async def get(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource:
        snapshot = await self._client.document(
            FirestorePaths.walkthrough(organisation_id, playbook_id, source_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"walkthrough {source_id} was not found")
        return _source(snapshot.to_dict())

    async def replace(
        self,
        current: WalkthroughSource,
        changed: WalkthroughSource,
    ) -> WalkthroughSource:
        if current.id != changed.id or _binding(current) != _binding(changed):
            raise StorageIntegrityError("walkthrough immutable binding changed")
        if changed.revision != current.revision + 1:
            raise StorageIntegrityError("walkthrough revision must advance exactly once")
        reference = self._client.document(
            FirestorePaths.walkthrough(current.organisation_id, current.playbook_id, current.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> WalkthroughSource:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"walkthrough {current.id} was not found")
            stored = _source(snapshot.to_dict())
            if stored.revision != current.revision:
                raise ResourceConflictError("walkthrough revision changed")
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))


def _source(value: object) -> WalkthroughSource:
    if not isinstance(value, dict):
        raise StorageIntegrityError("walkthrough has no data")
    return WalkthroughSource.model_validate(value)


def _binding(value: WalkthroughSource) -> tuple[object, ...]:
    return (
        value.id,
        value.organisation_id,
        value.playbook_id,
        value.kind,
        value.object_name,
        value.resource if value.object_name is None else None,
        value.content_type,
        value.size,
        value.crc32c,
        value.created_by,
    )
