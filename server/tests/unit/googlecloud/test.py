import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from connectors.google import GoogleRestClient
from connectors.googlecloud import GoogleCloudOnboardingConnector
from connectors.secrets import SecretManagerConnector
from contracts import (
    Connection,
    ConnectionRole,
    ConnectionStatus,
    GoogleCloudOnboardingSession,
    GoogleCloudOnboardingStatus,
    GoogleCloudProject,
)
from core.errors import ResourceConflictError
from core.googlecloud import GoogleCloudOnboardingService
from google.oauth2.credentials import Credentials

NOW = datetime(2026, 8, 22, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_onboarding_discovers_google_resources_without_persisting_token() -> None:
    repository = Repository()
    inventory = Inventory()
    validator = Validator()
    service = GoogleCloudOnboardingService(
        repository,
        Connector(),  # type: ignore[arg-type]
        "google-client",
        "https://app.firekey.example/google-cloud/callback",
        lambda: NOW,
        inventory,
        validator,
        "firekey-broker@firekey-host.iam.gserviceaccount.com",
    )
    session, state, verifier, authorization_url = await service.begin("org_one", "user-one")

    assert "accounts.google.com" in authorization_url
    assert "code_challenge_method=S256" in authorization_url
    assert state not in session.model_dump_json()
    assert verifier not in session.model_dump_json()

    with pytest.raises(ResourceConflictError, match="state is invalid"):
        await service.complete(
            "org_one", session.id, "user-one", "x" * len(state), verifier, "oauth-code"
        )

    completed, projects = await service.complete(
        "org_one", session.id, "user-one", state, verifier, "oauth-code"
    )

    assert completed.status is GoogleCloudOnboardingStatus.COMPLETE
    assert projects[0].project_id == "project-one"
    assert projects[0].services[0].region == "us-central1"
    assert projects[0].service_accounts[0].email.startswith("firekey-automation@")

    connection, command = await service.prepare_connection(
        "org_one",
        session.id,
        "user-one",
        "project-one",
        "firekey-automation@project-one.iam.gserviceaccount.com",
    )

    assert connection.platform == "google-cloud"
    assert connection.roles == frozenset({ConnectionRole.RUNTIME, ConnectionRole.SECRET_STORE})
    assert connection.status is ConnectionStatus.SETUP_REQUIRED
    assert "firekey-broker@firekey-host.iam.gserviceaccount.com" in command
    assert "firekey-api" not in command
    assert "firekey-coordinator" not in command

    ready = await service.verify_connection("org_one", session.id, "user-one", connection.revision)

    assert validator.validated == connection.id
    assert ready.status is ConnectionStatus.READY
    assert ready.revision == connection.revision + 1


@pytest.mark.anyio
async def test_connector_uses_short_lived_oauth_access_only_for_discovery() -> None:
    oauth_bodies: list[str] = []

    def google_cloud_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            oauth_bodies.append(request.content.decode())
            return httpx.Response(200, json={"access_token": "temporary-google-token"})
        assert request.headers["Authorization"] == "Bearer temporary-google-token"
        if request.url.path == "/v3/projects:search":
            return httpx.Response(
                200,
                json={
                    "projects": [
                        {
                            "name": "projects/123456789012",
                            "projectId": "project-one",
                            "displayName": "Project One",
                            "state": "ACTIVE",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/namespaces/project-one/services"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {
                                "name": "worker",
                                "labels": {"cloud.googleapis.com/location": "us-central1"},
                            },
                            "spec": {
                                "template": {
                                    "spec": {
                                        "serviceAccountName": (
                                            "worker@project-one.iam.gserviceaccount.com"
                                        )
                                    }
                                }
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith("/projects/project-one/serviceAccounts"):
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "email": "firekey-automation@project-one.iam.gserviceaccount.com",
                            "displayName": "FireKey automation",
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    def secret_manager_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"oauth-client-secret").decode()}},
        )

    google = GoogleRestClient(
        credentials=Credentials(token="platform-token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(secret_manager_handler)),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(google_cloud_handler))
    connector = GoogleCloudOnboardingConnector(
        "google-client",
        "projects/project-one/secrets/google-oauth/versions/1",
        "https://app.firekey.example/google-cloud/callback",
        SecretManagerConnector(google),
        client,
    )

    projects = await connector.discover("oauth-code", "pkce-verifier")

    encoded = json.dumps(projects)
    assert projects[0]["services"][0]["display_name"] == "worker"
    assert "temporary-google-token" not in encoded
    assert "oauth-client-secret" in oauth_bodies[0]
    await client.aclose()
    await google.close()


class Repository:
    def __init__(self) -> None:
        self.session: GoogleCloudOnboardingSession | None = None

    async def create_session(
        self, session: GoogleCloudOnboardingSession
    ) -> GoogleCloudOnboardingSession:
        self.session = session
        return session

    async def get_session(
        self, organisation_id: str, session_id: str
    ) -> GoogleCloudOnboardingSession:
        assert self.session is not None
        assert self.session.organisation_id == organisation_id
        assert self.session.id == session_id
        return self.session

    async def complete_session(
        self,
        session: GoogleCloudOnboardingSession,
        projects: tuple[GoogleCloudProject, ...],
        completed_at: datetime,
    ) -> GoogleCloudOnboardingSession:
        self.session = session.model_copy(
            update={
                "status": GoogleCloudOnboardingStatus.COMPLETE,
                "projects": projects,
                "completed_at": completed_at,
            }
        )
        return self.session

    async def attach_connection(
        self,
        session: GoogleCloudOnboardingSession,
        connection_id: str,
    ) -> GoogleCloudOnboardingSession:
        assert self.session == session
        self.session = session.model_copy(update={"connection_id": connection_id})
        return self.session


class Inventory:
    def __init__(self) -> None:
        self.connection: Connection | None = None

    async def add_connection(self, value: Connection) -> Connection:
        assert self.connection is None
        self.connection = value
        return value

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        assert self.connection is not None
        assert self.connection.organisation_id == organisation_id
        assert self.connection.id == resource_id
        return self.connection

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection:
        assert self.connection is not None
        assert self.connection.revision == expected_revision
        assert value.revision == expected_revision + 1
        self.connection = value
        return value


class Validator:
    def __init__(self) -> None:
        self.validated: str | None = None

    async def validate(self, connection: Connection) -> None:
        assert connection.status is ConnectionStatus.SETUP_REQUIRED
        self.validated = connection.id


class Connector:
    async def discover(self, code: str, verifier: str) -> tuple[dict[str, object], ...]:
        assert code == "oauth-code"
        assert verifier
        return (
            {
                "project_id": "project-one",
                "project_number": "123456789012",
                "display_name": "Project One",
                "services": (
                    {
                        "reference": "projects/project-one/locations/us-central1/services/worker",
                        "display_name": "worker",
                        "region": "us-central1",
                        "runtime_identity": "worker@project-one.iam.gserviceaccount.com",
                    },
                ),
                "service_accounts": (
                    {
                        "email": "firekey-automation@project-one.iam.gserviceaccount.com",
                        "display_name": "FireKey automation",
                    },
                ),
            },
        )
