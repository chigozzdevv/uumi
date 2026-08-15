from datetime import datetime
from typing import Any

from contracts import CorrelationCandidate, Incident, IncidentStatus, IngestionEvent
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.catalog import aggregate_count
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreIncidentRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]:
        incident_ref = self._client.document(
            FirestorePaths.incident(incident.organisation_id, incident.id)
        )
        event_ref = self._client.document(
            FirestorePaths.ingestion(
                event.organisation_id,
                event.source,
                event.source_event_id,
                event.kind,
            )
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[Incident, bool]:
            event_snapshot = await event_ref.get(transaction=transaction)
            incident_snapshot = await incident_ref.get(transaction=transaction)
            if event_snapshot.exists:
                existing_event = _data(event_snapshot)
                incident_id = existing_event.get("incident_id")
                if not isinstance(incident_id, str):
                    raise StorageIntegrityError("ingestion dedupe record has no incident")
                stored_event = IngestionEvent.model_validate(existing_event)
                if stored_event.stable_payload() != event.stable_payload():
                    raise ResourceConflictError(
                        "ingestion event identity was replayed with changes"
                    )
                existing = await self._client.document(
                    FirestorePaths.incident(event.organisation_id, incident_id)
                ).get(transaction=transaction)
                if not existing.exists:
                    raise StorageIntegrityError("deduplicated incident is missing")
                return Incident.model_validate(_data(existing)), False
            if incident_snapshot.exists:
                raise ResourceConflictError(f"incident ID {incident.id} already exists")
            transaction.create(event_ref, {**encode(event), "incident_id": incident.id})
            transaction.create(incident_ref, encode(incident))
            return incident, True

        return await apply(self._client.transaction(max_attempts=5))

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        snapshot = await self._client.document(
            FirestorePaths.incident(organisation_id, incident_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"incident {incident_id} was not found")
        return Incident.model_validate(_data(snapshot))

    async def list_incidents(self, organisation_id: str, limit: int) -> tuple[Incident, ...]:
        path = f"{FirestorePaths.organisation(organisation_id)}/incidents"
        incidents: list[Incident] = []
        async for snapshot in self._client.collection(path).limit(limit).stream():
            incidents.append(Incident.model_validate(_data(snapshot)))
        return tuple(incidents)

    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int:
        path = f"{FirestorePaths.organisation(organisation_id)}/incidents"
        query = self._client.collection(path).where(
            "status", "in", sorted(status.value for status in statuses)
        )
        return await aggregate_count(query)

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[CorrelationCandidate, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        status = IncidentStatus.ACTION if credential_id else IncidentStatus.CORRELATING
        return await self._update(
            organisation_id,
            incident_id,
            expected_revision,
            {
                "candidates": candidates,
                "credential_id": credential_id,
                "status": status,
                "updated_at": updated_at,
            },
        )

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident:
        return await self._update(
            organisation_id,
            incident_id,
            expected_revision,
            {
                "credential_id": credential_id,
                "run_id": run_id,
                "status": IncidentStatus.ROTATING,
                "updated_at": updated_at,
            },
        )

    async def advance_run(
        self,
        organisation_id: str,
        run_id: str,
        status: IncidentStatus,
        updated_at: datetime,
    ) -> tuple[Incident, ...]:
        root = f"{FirestorePaths.organisation(organisation_id)}/incidents"
        incidents = []
        async for snapshot in self._client.collection(root).where("run_id", "==", run_id).stream():
            current = Incident.model_validate(_data(snapshot))
            if current.status is status or (
                current.status is IncidentStatus.RESOLVED and status is IncidentStatus.CONTAINED
            ):
                incidents.append(current)
                continue
            allowed = (
                current.status is IncidentStatus.ROTATING
                if status is IncidentStatus.CONTAINED
                else current.status in {IncidentStatus.ROTATING, IncidentStatus.CONTAINED}
            )
            if not allowed:
                raise ResourceConflictError(
                    f"incident {current.id} cannot advance from {current.status.value}"
                )
            incidents.append(
                await self._update(
                    organisation_id,
                    current.id,
                    current.revision,
                    {"status": status, "updated_at": updated_at},
                )
            )
        return tuple(incidents)

    async def _update(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        values: dict[str, Any],
    ) -> Incident:
        reference = self._client.document(FirestorePaths.incident(organisation_id, incident_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Incident:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"incident {incident_id} was not found")
            current = Incident.model_validate(_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"incident expected revision {expected_revision}, found {current.revision}"
                )
            changed = current.model_copy(update={**values, "revision": current.revision + 1})
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"incident document {snapshot.id} has no data")
    return data
