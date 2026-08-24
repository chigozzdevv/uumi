import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from connectors.base import SecretValue
from connectors.notification import NotificationConnector
from contracts import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationEndpoint,
    NotificationKind,
    NotificationProvider,
    NotificationState,
    Severity,
)
from core.notification import NotificationDispatcher
from core.storage import NotificationClaim

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Deliveries:
    def __init__(self, claims: list[NotificationClaim]) -> None:
        self.claims = claims
        self.sent: list[str] = []
        self.failed: list[tuple[str, bool]] = []

    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[NotificationClaim, ...]:
        values, self.claims = self.claims[:limit], self.claims[limit:]
        return tuple(values)

    async def mark_sent(
        self,
        claim: NotificationClaim,
        owner_id: str,
        receipt: str,
        sent_at: datetime,
    ) -> None:
        self.sent.append(receipt)

    async def mark_failed(
        self,
        claim: NotificationClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
        terminal: bool,
    ) -> None:
        self.failed.append((error, terminal))


async def test_slack_delivery_contains_only_safe_message_and_app_link() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        if request.url.host == "secretmanager.googleapis.com":
            return httpx.Response(
                200,
                json={"payload": {"data": base64.b64encode(b"provider-auth").decode()}},
            )
        requests.append(request)
        return httpx.Response(200, text="ok")

    class SlackSecrets:
        async def access(self, version: str) -> SecretValue:
            return SecretValue(b"https://hooks.slack.com/services/T/B/value")

    connector = NotificationConnector(
        SlackSecrets(),
        "https://app.uumi.example",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    notification = _notification()
    endpoint = _endpoint()

    receipt = await connector.send(notification, endpoint, "delivery_one")

    assert receipt.startswith("accepted-")
    payload = requests[0].content.decode()
    assert "https://app.uumi.example/organisations/org_one/runs/run_one" in payload
    assert "token=" not in payload
    assert "provider-auth" not in payload
    await connector.close()


async def test_resend_delivery_is_idempotent_and_hides_the_api_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        requests.append(request)
        return httpx.Response(200, json={"id": "email_49a3999c"})

    class ResendSecrets:
        async def access(self, version: str) -> SecretValue:
            return SecretValue(b"re_provider_auth")

    connector = NotificationConnector(
        ResendSecrets(),
        "https://app.uumi.example",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    endpoint = NotificationEndpoint(
        id="endpoint_email",
        organisation_id="org_one",
        display_name="Operations email",
        channel=NotificationChannel.EMAIL,
        provider=NotificationProvider.RESEND,
        auth_reference="projects/project-one/secrets/notification/versions/1",
        event_kinds=frozenset({NotificationKind.ROTATION_FAILED}),
        recipients=("oncall@acme.example",),
        sender="Uumi <notifications@uumi.example>",
        created_at=NOW,
        updated_at=NOW,
    )

    receipt = await connector.send(_notification(), endpoint, "delivery_one")

    assert receipt == "email_49a3999c"
    request = requests[0]
    assert request.url.host == "api.resend.com"
    assert request.headers["Authorization"] == "Bearer re_provider_auth"
    assert request.headers["Idempotency-Key"] == "delivery_one"
    payload = request.content.decode()
    assert "https://app.uumi.example/organisations/org_one/runs/run_one" in payload
    assert "oncall@acme.example" in payload
    assert "re_provider_auth" not in payload
    await connector.close()


async def test_invitation_email_contains_only_invite_copy_and_auth_link() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        requests.append(request)
        return httpx.Response(200, json={"id": "email_invite"})

    class ResendSecrets:
        async def access(self, version: str) -> SecretValue:
            return SecretValue(b"re_provider_auth")

    connector = NotificationConnector(
        ResendSecrets(),
        "https://uumi.web.app",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    notification = Notification(
        id="notification_invite",
        organisation_id="org_one",
        kind=NotificationKind.TEAM_INVITATION,
        severity=Severity.LOW,
        title="Join Acme on Uumi",
        body="Chigozie invited you to join Acme as Viewer. The invitation expires in 7 days.",
        link_path="/auth",
        resource_id="member_one",
        created_at=NOW,
    )
    endpoint = NotificationEndpoint(
        id="endpoint_invite",
        organisation_id="org_one",
        display_name="new.member@acme.example",
        channel=NotificationChannel.EMAIL,
        provider=NotificationProvider.RESEND,
        auth_reference="projects/project-one/secrets/notification/versions/1",
        event_kinds=frozenset({NotificationKind.TEAM_INVITATION}),
        recipients=("new.member@acme.example",),
        sender="invite@uumi.example",
        created_at=NOW,
        updated_at=NOW,
    )

    receipt = await connector.send(notification, endpoint, "delivery_invite")

    assert receipt == "email_invite"
    payload = requests[0].content.decode()
    assert "https://uumi.web.app/auth" in payload
    assert "member_one" not in payload
    assert "Resource:" not in payload
    assert "re_provider_auth" not in payload
    await connector.close()


async def test_dispatcher_retries_only_retryable_failures() -> None:
    claim = _claim()
    repository = Deliveries([claim])

    class Failing:
        async def send(
            self,
            notification: Notification,
            endpoint: NotificationEndpoint,
            delivery_id: str,
        ) -> str:
            from connectors.base.errors import ConnectorError

            raise ConnectorError("temporary", "unavailable", retryable=True)

    summary = await NotificationDispatcher(
        repository,
        Failing(),
        "worker_one",
        lambda: NOW,
    ).drain()

    assert summary.failed == 1
    assert repository.failed == [("temporary: unavailable", False)]


def _notification() -> Notification:
    return Notification(
        id="notification_one",
        organisation_id="org_one",
        kind=NotificationKind.ROTATION_FAILED,
        severity=Severity.CRITICAL,
        title="Rotation failed",
        body="Run run_one requires review.",
        link_path="/organisations/org_one/runs/run_one",
        resource_id="run_one",
        run_id="run_one",
        created_at=NOW,
    )


def _endpoint() -> NotificationEndpoint:
    return NotificationEndpoint(
        id="endpoint_one",
        organisation_id="org_one",
        display_name="Operations",
        channel=NotificationChannel.CHAT,
        provider=NotificationProvider.SLACK,
        auth_reference="projects/project-one/secrets/notification/versions/1",
        event_kinds=frozenset({NotificationKind.ROTATION_FAILED}),
        created_at=NOW,
        updated_at=NOW,
    )


def _claim() -> NotificationClaim:
    notification = _notification()
    endpoint = _endpoint()
    delivery = NotificationDelivery(
        id="delivery_one",
        organisation_id="org_one",
        notification_id=notification.id,
        endpoint_id=endpoint.id,
        endpoint_revision=endpoint.revision,
        provider=endpoint.provider,
        state=NotificationState.SENDING,
        available_at=NOW,
        attempts=1,
        lease_owner="worker_one",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    return NotificationClaim("deliveries/one", delivery, notification, endpoint)
