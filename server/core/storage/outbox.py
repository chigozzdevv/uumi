from datetime import datetime, timedelta
from typing import Any

from contracts import OutboxEvent
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from core.errors import OutboxLeaseError, StorageIntegrityError
from core.events import OutboxClaim
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreOutboxRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[OutboxClaim, ...]:
        query = (
            self._client.collection_group("outbox")
            .where(filter=FieldFilter("published_at", "==", None))
            .where(filter=FieldFilter("available_at", "<=", now))
            .order_by("available_at")
            .limit(min(max(limit * 8, 64), 500))
        )
        claims: list[OutboxClaim] = []
        seen_runs: set[str] = set()

        async for snapshot in query.stream():
            candidate = OutboxEvent.model_validate(_required_data(snapshot))
            if candidate.event.run_id in seen_runs:
                continue
            claim = await self._claim_one(snapshot.reference.path, owner_id, now, lease_duration)
            if claim is not None:
                claims.append(claim)
                seen_runs.add(claim.outbox.event.run_id)
            if len(claims) == limit:
                break
        return tuple(claims)

    async def mark_published(
        self,
        claim: OutboxClaim,
        owner_id: str,
        message_id: str,
        published_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)
        cursor_ref = self._client.document(
            FirestorePaths.delivery(
                claim.outbox.event.organisation_id,
                claim.outbox.event.run_id,
            )
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            cursor = await cursor_ref.get(transaction=transaction)
            current = OutboxEvent.model_validate(_required_data(snapshot))
            if current.dead_lettered_at is not None:
                raise OutboxLeaseError(f"event {current.event.id} is dead-lettered")
            if current.published_at is not None:
                if current.publisher_message_id == message_id:
                    return
                raise OutboxLeaseError(f"event {current.event.id} is already published")
            _owned(current, owner_id)
            expected = current.event.revision - 1
            if _published_revision(cursor) != expected:
                raise OutboxLeaseError(f"event {current.event.id} is not next for its run")

            published = current.model_copy(
                update={
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "published_at": published_at,
                    "publisher_message_id": message_id,
                    "last_error": None,
                }
            )
            transaction.set(reference, encode(published))
            transaction.set(
                cursor_ref,
                {
                    "organisation_id": current.event.organisation_id,
                    "run_id": current.event.run_id,
                    "published_revision": current.event.revision,
                    "event_id": current.event.id,
                    "message_id": message_id,
                    "published_at": published_at,
                },
            )

        await apply(self._client.transaction(max_attempts=5))

    async def mark_dead_letter(
        self,
        claim: OutboxClaim,
        owner_id: str,
        error: str,
        dead_lettered_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)
        cursor_ref = self._client.document(
            FirestorePaths.delivery(
                claim.outbox.event.organisation_id,
                claim.outbox.event.run_id,
            )
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            cursor = await cursor_ref.get(transaction=transaction)
            current = OutboxEvent.model_validate(_required_data(snapshot))
            if current.dead_lettered_at is not None:
                return
            if current.published_at is not None:
                raise OutboxLeaseError(f"event {current.event.id} is already published")
            _owned(current, owner_id)
            if _published_revision(cursor) != current.event.revision - 1:
                raise OutboxLeaseError(f"event {current.event.id} is not next for its run")
            changed = current.model_copy(
                update={
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "dead_lettered_at": dead_lettered_at,
                    "dead_letter_reason": error,
                    "last_error": error,
                }
            )
            transaction.set(reference, encode(changed))
            transaction.set(
                cursor_ref,
                {
                    "organisation_id": current.event.organisation_id,
                    "run_id": current.event.run_id,
                    "published_revision": current.event.revision,
                    "event_id": current.event.id,
                    "dead_lettered_at": dead_lettered_at,
                    "last_error": error,
                },
            )

        await apply(self._client.transaction(max_attempts=5))

    async def mark_failed(
        self,
        claim: OutboxClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            current = OutboxEvent.model_validate(_required_data(snapshot))
            if current.published_at is not None or current.dead_lettered_at is not None:
                return
            _owned(current, owner_id)
            failed = current.model_copy(
                update={
                    "available_at": available_at,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_error": error,
                }
            )
            transaction.set(reference, encode(failed))

        await apply(self._client.transaction(max_attempts=5))

    async def _claim_one(
        self,
        path: str,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> OutboxClaim | None:
        reference = self._client.document(path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> OutboxClaim | None:
            snapshot = await reference.get(transaction=transaction)
            current = OutboxEvent.model_validate(_required_data(snapshot))
            cursor_ref = self._client.document(
                FirestorePaths.delivery(
                    current.event.organisation_id,
                    current.event.run_id,
                )
            )
            cursor = await cursor_ref.get(transaction=transaction)
            if (
                current.published_at is not None
                or current.dead_lettered_at is not None
                or current.available_at > now
            ):
                return None
            if current.lease_expires_at is not None and current.lease_expires_at > now:
                return None
            if _published_revision(cursor) != current.event.revision - 1:
                return None

            claimed = current.model_copy(
                update={
                    "attempts": current.attempts + 1,
                    "lease_owner": owner_id,
                    "lease_expires_at": now + lease_duration,
                }
            )
            transaction.set(reference, encode(claimed))
            return OutboxClaim(path=path, outbox=claimed)

        return await apply(self._client.transaction(max_attempts=5))


def _published_revision(snapshot: DocumentSnapshot) -> int:
    if not snapshot.exists:
        return -1
    value = _required_data(snapshot).get("published_revision")
    if not isinstance(value, int):
        raise StorageIntegrityError("delivery cursor revision is missing or invalid")
    return value


def _owned(event: OutboxEvent, owner_id: str) -> None:
    if event.lease_owner != owner_id:
        raise OutboxLeaseError(f"event {event.event.id} is not leased by {owner_id}")


def _required_data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"document {snapshot.id} has no data")
    return data
