from datetime import UTC, datetime

import pytest
from contracts import (
    Notification,
    NotificationEndpoint,
    NotificationKind,
    Severity,
)
from core.notification import NotificationService
from pydantic import ValidationError

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Notifications:
    def __init__(self) -> None:
        self.values: dict[str, Notification] = {}

    async def emit(self, notification: Notification) -> tuple[Notification, bool]:
        existing = self.values.get(notification.id)
        if existing is not None:
            return existing, False
        self.values[notification.id] = notification
        return notification, True

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]:
        return tuple(self.values.values())[:limit]

    async def mark_read(
        self,
        organisation_id: str,
        notification_id: str,
        expected_revision: int,
        read_at: datetime,
    ) -> Notification:
        current = self.values[notification_id]
        changed = current.model_copy(update={"read_at": read_at, "revision": 1})
        self.values[notification_id] = changed
        return changed

    async def register_endpoint(
        self, endpoint: NotificationEndpoint
    ) -> NotificationEndpoint:
        return endpoint

    async def list_endpoints(
        self, organisation_id: str
    ) -> tuple[NotificationEndpoint, ...]:
        return ()

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint:
        raise NotImplementedError


async def test_emission_is_deterministic_and_links_cannot_carry_capabilities() -> None:
    repository = Notifications()
    service = NotificationService(repository, lambda: NOW)

    first, applied = await service.emit(
        "event_one",
        "org_one",
        NotificationKind.INCIDENT,
        Severity.CRITICAL,
        "Credential exposed",
        "Incident incident_one requires containment.",
        "/organisations/org_one/incidents/incident_one",
        "incident_one",
        incident_id="incident_one",
    )
    duplicate, duplicate_applied = await service.emit(
        "event_one",
        "org_one",
        NotificationKind.INCIDENT,
        Severity.CRITICAL,
        "Credential exposed",
        "Incident incident_one requires containment.",
        "/organisations/org_one/incidents/incident_one",
        "incident_one",
        incident_id="incident_one",
    )

    assert applied and not duplicate_applied
    assert first.id == duplicate.id
    with pytest.raises(ValidationError, match="capabilities"):
        Notification.model_validate(
            {**first.model_dump(), "link_path": "/approvals/one?token=secret"}
        )
