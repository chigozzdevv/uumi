from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    Confidence,
    CorrelationCandidate,
    Incident,
    IncidentStatus,
    IngestionEvent,
)

from core.errors import ResourceConflictError


class IncidentRepository(Protocol):
    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]: ...

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[CorrelationCandidate, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident: ...

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident: ...


class IncidentService:
    def __init__(
        self,
        repository: IncidentRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def ingest(self, incident_id: str, event: IngestionEvent) -> tuple[Incident, bool]:
        incident = Incident(
            id=incident_id,
            organisation_id=event.organisation_id,
            event_id=event.id,
            source=event.source,
            source_event_id=event.source_event_id,
            severity=event.severity,
            confidence=event.confidence,
            status=IncidentStatus.NEW,
            resource=event.resource,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        return await self._repository.ingest(incident, event)

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[CorrelationCandidate, ...],
    ) -> Incident:
        verified = tuple(
            candidate for candidate in candidates if candidate.confidence is Confidence.VERIFIED
        )
        if len(verified) > 1:
            raise ResourceConflictError("an incident cannot have multiple verified credentials")
        credential_id = verified[0].credential_id if verified else None
        return await self._repository.correlate(
            organisation_id,
            incident_id,
            expected_revision,
            candidates,
            credential_id,
            self._clock(),
        )

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
    ) -> Incident:
        return await self._repository.link_run(
            organisation_id,
            incident_id,
            expected_revision,
            credential_id,
            run_id,
            self._clock(),
        )
