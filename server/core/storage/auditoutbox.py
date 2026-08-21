from datetime import datetime, timedelta
from typing import Any

from contracts import AuditOutbox
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from core.audit.delivery import AuditClaim
from core.errors import ResourceConflictError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreAuditOutboxRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[AuditClaim, ...]:
        query = (
            self._client.collection_group("audit-outbox")
            .where(filter=FieldFilter("logged_at", "==", None))
            .where(filter=FieldFilter("available_at", "<=", now))
            .order_by("available_at")
            .limit(min(max(limit * 8, 64), 500))
        )
        claims: list[AuditClaim] = []
        seen: set[str] = set()
        async for snapshot in query.stream():
            candidate = AuditOutbox.model_validate(_data(snapshot))
            organisation_id = candidate.event.organisation_id
            if organisation_id in seen:
                continue
            claim = await self._claim_one(snapshot.reference.path, owner_id, now, lease_duration)
            if claim is not None:
                claims.append(claim)
                seen.add(organisation_id)
            if len(claims) == limit:
                break
        return tuple(claims)

    async def mark_logged(
        self,
        claim: AuditClaim,
        owner_id: str,
        receipt: str,
        logged_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)
        event = claim.outbox.event
        cursor_ref = self._client.document(FirestorePaths.audit_delivery(event.organisation_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            cursor = await cursor_ref.get(transaction=transaction)
            current = AuditOutbox.model_validate(_data(snapshot))
            if current.dead_lettered_at is not None:
                raise ResourceConflictError("audit event is dead-lettered")
            if current.logged_at is not None:
                if current.provider_receipt == receipt:
                    return
                raise ResourceConflictError("audit event already has another log receipt")
            _owned(current, owner_id)
            if _logged_sequence(cursor) != current.event.sequence - 1:
                raise ResourceConflictError("audit event is not next for its organisation")
            changed = current.model_copy(
                update={
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "logged_at": logged_at,
                    "provider_receipt": receipt,
                    "last_error": None,
                }
            )
            transaction.set(reference, encode(changed))
            transaction.set(
                cursor_ref,
                {
                    "organisation_id": event.organisation_id,
                    "logged_sequence": event.sequence,
                    "event_id": event.id,
                    "event_hash": event.event_hash,
                    "provider_receipt": receipt,
                    "logged_at": logged_at,
                },
            )

        await apply(self._client.transaction(max_attempts=5))

    async def mark_dead_letter(
        self,
        claim: AuditClaim,
        owner_id: str,
        error: str,
        dead_lettered_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)
        event = claim.outbox.event
        cursor_ref = self._client.document(FirestorePaths.audit_delivery(event.organisation_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            cursor = await cursor_ref.get(transaction=transaction)
            current = AuditOutbox.model_validate(_data(snapshot))
            if current.dead_lettered_at is not None:
                return
            if current.logged_at is not None:
                raise ResourceConflictError("audit event is already logged")
            _owned(current, owner_id)
            if _logged_sequence(cursor) != current.event.sequence - 1:
                raise ResourceConflictError("audit event is not next for its organisation")
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
                    "organisation_id": event.organisation_id,
                    "logged_sequence": event.sequence,
                    "event_id": event.id,
                    "event_hash": event.event_hash,
                    "dead_lettered_at": dead_lettered_at,
                    "last_error": error,
                },
            )

        await apply(self._client.transaction(max_attempts=5))

    async def mark_failed(
        self,
        claim: AuditClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            current = AuditOutbox.model_validate(_data(snapshot))
            if current.logged_at is not None or current.dead_lettered_at is not None:
                return
            _owned(current, owner_id)
            changed = current.model_copy(
                update={
                    "available_at": available_at,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_error": error,
                }
            )
            transaction.set(reference, encode(changed))

        await apply(self._client.transaction(max_attempts=5))

    async def _claim_one(
        self,
        path: str,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> AuditClaim | None:
        reference = self._client.document(path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> AuditClaim | None:
            snapshot = await reference.get(transaction=transaction)
            current = AuditOutbox.model_validate(_data(snapshot))
            cursor_ref = self._client.document(
                FirestorePaths.audit_delivery(current.event.organisation_id)
            )
            cursor = await cursor_ref.get(transaction=transaction)
            if (
                current.logged_at is not None
                or current.dead_lettered_at is not None
                or current.available_at > now
            ):
                return None
            if current.lease_expires_at is not None and current.lease_expires_at > now:
                return None
            if _logged_sequence(cursor) != current.event.sequence - 1:
                return None
            claimed = current.model_copy(
                update={
                    "attempts": current.attempts + 1,
                    "lease_owner": owner_id,
                    "lease_expires_at": now + lease_duration,
                }
            )
            transaction.set(reference, encode(claimed))
            return AuditClaim(path, claimed)

        return await apply(self._client.transaction(max_attempts=5))


def _logged_sequence(snapshot: DocumentSnapshot) -> int:
    if not snapshot.exists:
        return -1
    value = _data(snapshot).get("logged_sequence")
    if not isinstance(value, int):
        raise StorageIntegrityError("audit delivery cursor sequence is invalid")
    return value


def _owned(outbox: AuditOutbox, owner_id: str) -> None:
    if outbox.lease_owner != owner_id:
        raise ResourceConflictError("audit delivery lease is not owned")


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"audit outbox document {snapshot.id} has no data")
    return data
