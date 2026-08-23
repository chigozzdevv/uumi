import base64
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from connectors.github import GitHubOnboardingConnector, GitHubWebhook
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from contracts import (
    GitHubInstallation,
    GitHubOnboardingSession,
    GitHubOnboardingStatus,
    GitHubRepository,
    GitHubRepositoryCandidate,
    GitHubWebhookReceipt,
)
from core.errors import ResourceConflictError
from core.github import GitHubOnboardingService
from google.oauth2.credentials import Credentials

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_onboarding_verifies_repositories_and_signed_delivery() -> None:
    repository = Repository(
        GitHubWebhookReceipt(
            installation_id=123,
            delivery_id="delivery-one",
            event="installation",
            action="created",
            received_at=NOW,
        )
    )
    service = GitHubOnboardingService(
        repository,
        Connector(),  # type: ignore[arg-type]
        "firekey-app",
        "client-one",
        "https://app.firekey.example/github/callback",
        lambda: NOW,
    )
    session, state, verifier, installation_url, authorization_url = await service.begin(
        "org_one", "user-one"
    )

    assert "state=" in installation_url
    assert "code_challenge_method=S256" in authorization_url
    assert state not in session.model_dump_json()
    assert verifier not in session.model_dump_json()

    with pytest.raises(ResourceConflictError, match="state is invalid"):
        await service.discover(
            "org_one",
            session.id,
            "user-one",
            "x" * len(state),
            verifier,
            "oauth-code",
            123,
        )

    staged, installation, candidates = await service.discover(
        "org_one",
        session.id,
        "user-one",
        state,
        verifier,
        "oauth-code",
        123,
    )

    assert staged.status is GitHubOnboardingStatus.DISCOVERED
    assert candidates[0].full_name == "customer/api"

    completed, installation, repositories = await service.complete(
        "org_one",
        session.id,
        "user-one",
    )

    assert completed.status is GitHubOnboardingStatus.COMPLETE
    assert installation.ready
    assert installation.webhook_verified_at == NOW
    assert repositories[0].full_name == "customer/api"


@pytest.mark.anyio
async def test_connector_uses_user_access_to_verify_installation_without_persisting_token() -> None:
    oauth_bodies: list[str] = []

    def github_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            oauth_bodies.append(request.content.decode())
            return httpx.Response(200, json={"access_token": "temporary-user-token"})
        assert request.headers["Authorization"] == "Bearer temporary-user-token"
        if request.url.path == "/user/installations/123":
            return httpx.Response(
                200,
                json={
                    "id": 123,
                    "account": {"id": 44, "login": "customer", "type": "Organization"},
                    "repository_selection": "selected",
                    "permissions": {"secret_scanning_alerts": "read"},
                    "events": ["secret_scanning_alert"],
                },
            )
        if request.url.path == "/user/installations/123/repositories":
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "id": 456,
                            "full_name": "customer/api",
                            "private": True,
                            "default_branch": "main",
                        }
                    ]
                },
            )
        if request.url.path == "/repos/customer/api/secret-scanning/alerts":
            assert request.url.params["hide_secret"] == "true"
            return httpx.Response(200, json=[])
        raise AssertionError(request.url)

    def google_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"oauth-client-secret").decode()}},
        )

    google = GoogleRestClient(
        credentials=Credentials(token="google-token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(google_handler)),
    )
    github = httpx.AsyncClient(transport=httpx.MockTransport(github_handler))
    connector = GitHubOnboardingConnector(
        "client-one",
        "projects/project-one/secrets/github-oauth/versions/1",
        "https://app.firekey.example/github/callback",
        SecretManagerConnector(google),
        github,
    )

    installation, repositories = await connector.verify("code-one", "verifier-one", 123)

    encoded = json.dumps({"installation": installation, "repositories": repositories})
    assert installation["account_login"] == "customer"
    assert repositories[0]["secret_scanning"] == "enabled"
    assert "temporary-user-token" not in encoded
    assert "oauth-client-secret" in oauth_bodies[0]
    await github.aclose()
    await google.close()


def test_secret_scanning_event_uses_the_confirmed_source_connection() -> None:
    body = json.dumps(
        {
            "action": "created",
            "installation": {"id": 123},
            "repository": {"id": 456, "full_name": "customer/api"},
            "alert": {
                "number": 7,
                "html_url": "https://github.com/customer/api/security/secret-scanning/7",
                "secret_type": "customer_platform_token",
                "secret": "must-never-enter-firekey-metadata",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
        }
    ).encode()

    event = GitHubWebhook().normalise(
        "org_one",
        "secret_scanning_alert",
        body,
        NOW,
        "connection_github",
    )

    assert event.resource.connection_id == "connection_github"
    assert event.resource.credential_id is None
    assert event.resource.repository == "customer/api"
    assert event.resource.provider is None
    assert "must-never-enter-firekey-metadata" not in event.model_dump_json()


def test_public_leak_event_is_an_exposure_trigger() -> None:
    body = json.dumps(
        {
            "action": "publicly_leaked",
            "repository": {"full_name": "customer/api"},
            "alert": {
                "number": 8,
                "html_url": "https://github.com/customer/api/security/secret-scanning/8",
                "secret_type": "another_customer_secret",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
        }
    ).encode()

    event = GitHubWebhook().normalise(
        "org_one", "secret_scanning_alert", body, NOW, "connection_github"
    )

    assert event.kind == "credential-exposure-detected"
    assert event.severity.value == "critical"


class Connector:
    async def verify(
        self, code: str, verifier: str, installation_id: int
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        assert code == "oauth-code"
        assert verifier
        assert installation_id == 123
        return (
            {
                "installation_id": 123,
                "account_id": 44,
                "account_login": "customer",
                "account_type": "Organization",
                "repository_selection": "selected",
                "permissions": {"secret_scanning_alerts": "read"},
                "events": ["secret_scanning_alert"],
            },
            (
                {
                    "repository_id": 456,
                    "full_name": "customer/api",
                    "private": True,
                    "default_branch": "main",
                    "secret_scanning": "enabled",
                },
            ),
        )


class Repository:
    def __init__(self, receipt: GitHubWebhookReceipt | None) -> None:
        self.receipt_value = receipt
        self.session: GitHubOnboardingSession | None = None
        self.installation_value: GitHubInstallation | None = None
        self.repository_values: tuple[GitHubRepository, ...] = ()

    async def create_session(self, session: GitHubOnboardingSession) -> GitHubOnboardingSession:
        self.session = session
        return session

    async def get_session(self, organisation_id: str, session_id: str) -> GitHubOnboardingSession:
        assert self.session is not None
        return self.session

    async def receipt(self, installation_id: int) -> GitHubWebhookReceipt | None:
        return self.receipt_value

    async def stage(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepositoryCandidate, ...],
    ) -> GitHubOnboardingSession:
        staged = session.model_copy(
            update={
                "status": GitHubOnboardingStatus.DISCOVERED,
                "installation_id": installation.installation_id,
                "installation": installation,
                "repositories": repositories,
            }
        )
        self.session = staged
        return staged

    async def complete(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepository, ...],
    ) -> GitHubOnboardingSession:
        completed = session.model_copy(
            update={
                "status": GitHubOnboardingStatus.COMPLETE,
                "installation_id": installation.installation_id,
                "completed_at": installation.updated_at,
            }
        )
        self.session = completed
        self.installation_value = installation
        self.repository_values = repositories
        return completed

    async def installation(self, organisation_id: str, installation_id: int) -> GitHubInstallation:
        assert self.installation_value is not None
        return self.installation_value

    async def repositories(
        self, organisation_id: str, installation_id: int
    ) -> tuple[GitHubRepository, ...]:
        return self.repository_values
