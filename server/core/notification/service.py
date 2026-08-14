import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    Notification,
    NotificationChannel,
    NotificationEndpoint,
    NotificationKind,
    NotificationProvider,
    Severity,
)


class NotificationRepository(Protocol):
    async def emit(self, notification: Notification) -> tuple[Notification, bool]: ...

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]: ...

    async def mark_read(
        self,
        organisation_id: str,
        notification_id: str,
        expected_revision: int,
        read_at: datetime,
    ) -> Notification: ...

    async def register_endpoint(
        self, endpoint: NotificationEndpoint
    ) -> NotificationEndpoint: ...

    async def list_endpoints(
        self, organisation_id: str
    ) -> tuple[NotificationEndpoint, ...]: ...

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint: ...


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def emit(
        self,
        event_id: str,
        organisation_id: str,
        kind: NotificationKind,
        severity: Severity,
        title: str,
        body: str,
        link_path: str,
        resource_id: str,
        run_id: str | None = None,
        incident_id: str | None = None,
        approval_id: str | None = None,
    ) -> tuple[Notification, bool]:
        identity = hashlib.sha256(
            f"{organisation_id}\0{event_id}\0{kind.value}".encode()
        ).hexdigest()
        notification = Notification(
            id=f"notification_{identity[:40]}",
            organisation_id=organisation_id,
            kind=kind,
            severity=severity,
            title=title,
            body=body,
            link_path=link_path,
            resource_id=resource_id,
            run_id=run_id,
            incident_id=incident_id,
            approval_id=approval_id,
            created_at=self._clock(),
        )
        return await self._repository.emit(notification)

    async def list_notifications(
        self, organisation_id: str, limit: int = 100
    ) -> tuple[Notification, ...]:
        return await self._repository.list_notifications(organisation_id, limit)

    async def mark_read(
        self, organisation_id: str, notification_id: str, expected_revision: int
    ) -> Notification:
        return await self._repository.mark_read(
            organisation_id,
            notification_id,
            expected_revision,
            self._clock(),
        )

    async def register_endpoint(
        self,
        endpoint_id: str,
        organisation_id: str,
        display_name: str,
        channel: NotificationChannel,
        provider: NotificationProvider,
        auth_reference: str,
        event_kinds: frozenset[NotificationKind],
        recipients: tuple[str, ...] = (),
        sender: str | None = None,
    ) -> NotificationEndpoint:
        now = self._clock()
        endpoint = NotificationEndpoint(
            id=endpoint_id,
            organisation_id=organisation_id,
            display_name=display_name,
            channel=channel,
            provider=provider,
            auth_reference=auth_reference,
            event_kinds=event_kinds,
            recipients=recipients,
            sender=sender,
            created_at=now,
            updated_at=now,
        )
        return await self._repository.register_endpoint(endpoint)

    async def list_endpoints(
        self, organisation_id: str
    ) -> tuple[NotificationEndpoint, ...]:
        return await self._repository.list_endpoints(organisation_id)

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
    ) -> NotificationEndpoint:
        return await self._repository.set_endpoint_enabled(
            organisation_id,
            endpoint_id,
            expected_revision,
            enabled,
            self._clock(),
        )
