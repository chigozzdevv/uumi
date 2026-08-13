from collections.abc import Callable
from typing import Any, TypeVar

from contracts import Contract
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from pydantic import TypeAdapter

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode

T = TypeVar("T", bound=Contract)


class FirestoreCatalog:
    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    async def create(self, path: str, value: Contract) -> None:
        reference = self.client.document(path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            current = await reference.get(transaction=transaction)
            if current.exists:
                raise ResourceConflictError(f"resource {path} already exists")
            transaction.create(reference, encode(value))

        await apply(self.client.transaction(max_attempts=5))

    async def get(self, path: str, model: type[T]) -> T:
        snapshot = await self.client.document(path).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"resource {path} was not found")
        data = snapshot.to_dict()
        if data is None:
            raise StorageIntegrityError(f"resource {path} has no data")
        return model.model_validate(data)

    async def replace(
        self,
        path: str,
        model: type[T],
        expected_revision: int,
        update: Callable[[T], T],
    ) -> T:
        reference = self.client.document(path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> T:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"resource {path} was not found")
            data = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"resource {path} has no data")
            current = model.model_validate(data)
            revision = getattr(current, "revision", None)
            if revision != expected_revision:
                raise ResourceConflictError(
                    f"resource {path} expected revision {expected_revision}, found {revision}"
                )
            changed = update(current)
            changed_revision = getattr(changed, "revision", None)
            if changed_revision != expected_revision + 1:
                raise StorageIntegrityError("catalog updates must advance revision exactly once")
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self.client.transaction(max_attempts=5))

    async def list(self, path: str, model: type[T], limit: int = 200) -> tuple[T, ...]:
        adapter = TypeAdapter(model)
        values: list[T] = []
        async for snapshot in self.client.collection(path).limit(limit).stream():
            data: dict[str, Any] | None = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"resource {snapshot.reference.path} has no data")
            values.append(adapter.validate_python(data))
        return tuple(values)
