from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import AuditEvent
from telemetry import redact


class AuditRepository(Protocol):
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
    ) -> AuditEvent: ...


class AuditWriter:
    def __init__(
        self,
        repository: AuditRepository,
        region: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._region = region
        self._clock = clock

    async def append(
        self,
        event_id: str,
        organisation_id: str,
        kind: str,
        actor_id: str,
        resource: str,
        payload: dict[str, str | int | float | bool | None],
        run_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        safe = redact(payload)
        if not isinstance(safe, dict):
            raise TypeError("audit payload redaction must preserve its mapping")
        return await self._repository.append(
            event_id,
            organisation_id,
            kind,
            actor_id,
            resource,
            run_id,
            safe,
            evidence_ids,
            occurred_at or self._clock(),
            self._region,
        )
