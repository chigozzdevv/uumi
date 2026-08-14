from datetime import datetime
from typing import Any

from contracts import AuditEvent, AuditOutbox
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.audit.chain import GENESIS, event_hash
from core.errors import ResourceConflictError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreAuditRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def append(
        self,
        event_id: str,
        organisation_id: str,
        kind: str,
        actor_id: str,
        resource: str,
        run_id: str | None,
        payload: dict[str, str | int | float | bool | None],
        evidence_ids: tuple[str, ...],
        occurred_at: datetime,
        region: str,
    ) -> AuditEvent:
        event_ref = self._client.document(FirestorePaths.audit(organisation_id, event_id))
        outbox_ref = self._client.document(FirestorePaths.audit_outbox(organisation_id, event_id))
        head_ref = self._client.document(FirestorePaths.audit_head(organisation_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> AuditEvent:
            existing = await event_ref.get(transaction=transaction)
            existing_outbox = await outbox_ref.get(transaction=transaction)
            head = await head_ref.get(transaction=transaction)
            if existing.exists:
                current = AuditEvent.model_validate(_data(existing))
                fields = (
                    current.kind,
                    current.actor_id,
                    current.resource,
                    current.run_id,
                    current.evidence_ids,
                    current.region,
                )
                if (
                    fields
                    == (
                        kind,
                        actor_id,
                        resource,
                        run_id,
                        evidence_ids,
                        region,
                    )
                    and current.payload == payload
                ):
                    if existing_outbox.exists:
                        outbox = AuditOutbox.model_validate(_data(existing_outbox))
                        if outbox.event != current:
                            raise StorageIntegrityError(
                                "audit outbox event does not match its index"
                            )
                    else:
                        transaction.create(
                            outbox_ref,
                            encode(AuditOutbox(event=current, available_at=current.occurred_at)),
                        )
                    return current
                raise ResourceConflictError(f"audit event {event_id} already exists")
            if head.exists:
                head_data = _data(head)
                sequence_value = head_data.get("sequence")
                hash_value = head_data.get("event_hash")
                if not isinstance(sequence_value, int) or not isinstance(hash_value, str):
                    raise StorageIntegrityError("audit chain head is invalid")
                sequence = sequence_value + 1
                previous = hash_value
            else:
                sequence = 0
                previous = GENESIS
            checksum = event_hash(
                organisation_id,
                sequence,
                kind,
                actor_id,
                resource,
                run_id,
                payload,
                evidence_ids,
                previous,
                occurred_at,
                region,
            )
            event = AuditEvent(
                id=event_id,
                organisation_id=organisation_id,
                sequence=sequence,
                kind=kind,
                actor_id=actor_id,
                resource=resource,
                run_id=run_id,
                payload=payload,
                evidence_ids=evidence_ids,
                previous_hash=previous,
                event_hash=checksum,
                occurred_at=occurred_at,
                region=region,
            )
            transaction.create(event_ref, encode(event))
            transaction.create(
                outbox_ref,
                encode(AuditOutbox(event=event, available_at=occurred_at)),
            )
            transaction.set(
                head_ref,
                {
                    "organisation_id": organisation_id,
                    "sequence": sequence,
                    "event_id": event_id,
                    "event_hash": checksum,
                    "occurred_at": occurred_at,
                },
            )
            return event

        return await apply(self._client.transaction(max_attempts=8))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"audit document {snapshot.id} has no data")
    return data
