from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from connectors.logging import CloudLoggingConnector
from contracts import AuditEvent, AuditOutbox
from core.audit import AuditClaim, AuditPublisher

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
HASH = "a" * 64
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Google:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "POST"
        assert url.endswith("/entries:write")
        self.payload = kwargs["json"]
        return {}


class Outbox:
    def __init__(self, claims: list[AuditClaim]) -> None:
        self.claims = claims
        self.logged: list[str] = []
        self.failed: list[str] = []

    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[AuditClaim, ...]:
        values, self.claims = self.claims[:limit], self.claims[limit:]
        return tuple(values)

    async def mark_logged(
        self,
        claim: AuditClaim,
        owner_id: str,
        receipt: str,
        logged_at: datetime,
    ) -> None:
        self.logged.append(receipt)

    async def mark_failed(
        self,
        claim: AuditClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
    ) -> None:
        self.failed.append(error)


async def test_cloud_logging_uses_event_hash_as_deterministic_insert_id() -> None:
    google = Google()
    connector = CloudLoggingConnector(google, "project-one")  # type: ignore[arg-type]
    event = _event()

    receipt = await connector.write(event)

    assert receipt == HASH
    entry = google.payload["entries"][0]
    assert entry["insertId"] == HASH
    assert entry["jsonPayload"]["organisation_id"] == "org_one"
    assert entry["jsonPayload"]["previous_hash"] == "0" * 64


async def test_audit_publisher_drains_ordered_claims() -> None:
    claim = AuditClaim(
        "organisations/org_one/audit-outbox/audit_one",
        AuditOutbox(
            event=_event(),
            available_at=NOW,
            attempts=1,
            lease_owner="auditlog_one",
            lease_expires_at=NOW + timedelta(minutes=1),
        ),
    )
    repository = Outbox([claim])

    class Transport:
        async def write(self, event: AuditEvent) -> str:
            return event.event_hash

    summary = await AuditPublisher(
        repository,
        Transport(),
        "auditlog_one",
        lambda: NOW,
    ).drain()

    assert summary.logged == 1
    assert repository.logged == [HASH]


def _event() -> AuditEvent:
    return AuditEvent(
        id="audit_one",
        organisation_id="org_one",
        sequence=0,
        kind="run.created",
        actor_id="actor_one",
        resource="runs/run_one",
        run_id="run_one",
        payload={"status": "pending"},
        previous_hash="0" * 64,
        event_hash=HASH,
        occurred_at=NOW,
        region="us-east1",
    )
