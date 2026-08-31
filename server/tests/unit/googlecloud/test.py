import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from connectors.base import SecretValue
from connectors.google import GoogleRestClient
from connectors.googlecloud import GoogleCloudDiscovery, GoogleCloudOnboardingConnector
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
from core.googlecloud import GoogleCloudAuthorizationCipher, GoogleCloudOnboardingService
from google.oauth2.credentials import Credentials

NOW = datetime(2026, 8, 22, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_google_cloud_project_accepts_google_managed_service_accounts() -> None:
    project = GoogleCloudProject.model_validate(
        {
            "project_id": "project-one",
            "project_number": "123456789012",
            "display_name": "Project One",
            "service_accounts": [
                {
                    "email": "256626005636-compute@developer.gserviceaccount.com",
                    "display_name": "Compute Engine default service account",
                }
            ],
        }
    )

    assert project.service_accounts[0].email.startswith("256626005636-compute@")


@pytest.mark.anyio
async def test_onboarding_discovers_google_resources_without_persisting_token() -> None:
    repository = Repository()
    inventory = Inventory()
    validator = Validator()
    service = GoogleCloudOnboardingService(
        repository,
        Connector(),  # type: ignore[arg-type]
        "google-client",
        "https://app.uumi.example/google-cloud/callback",
        lambda: NOW,
        inventory,
        validator,
        "uumi-broker@uumi-host.iam.gserviceaccount.com",
        Cipher(),  # type: ignore[arg-type]
        "uumi-api@uumi-host.iam.gserviceaccount.com",
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
    assert projects[0].service_accounts[0].email.startswith("uumi-automation@")

    connection = await service.prepare_connection(
        "org_one",
        session.id,
        "user-one",
        "project-one",
        "uumi-automation@project-one.iam.gserviceaccount.com",
    )

    assert connection.platform == "google-cloud"
    assert connection.roles == frozenset({ConnectionRole.RUNTIME, ConnectionRole.SECRET_STORE})
    assert connection.status is ConnectionStatus.SETUP_REQUIRED
    assert connection.allowed_resources == (
        "projects/project-one/locations/us-central1/services",
        "projects/project-one/secrets",
    )
    ready = await service.authorize_connection(
        "org_one", session.id, "user-one", connection.revision
    )

    assert validator.validated == connection.id
    assert ready.status is ConnectionStatus.READY
    assert ready.revision == connection.revision + 1


@pytest.mark.anyio
async def test_onboarding_reuses_matching_setup_required_connection() -> None:
    repository = Repository()
    inventory = Inventory()
    service = GoogleCloudOnboardingService(
        repository,
        Connector(),  # type: ignore[arg-type]
        "google-client",
        "https://app.uumi.example/google-cloud/callback",
        lambda: NOW,
        inventory,
        Validator(),
        "uumi-broker@uumi-host.iam.gserviceaccount.com",
        Cipher(),  # type: ignore[arg-type]
    )

    async def prepare() -> Connection:
        session, state, verifier, _ = await service.begin("org_one", "user-one")
        await service.complete("org_one", session.id, "user-one", state, verifier, "oauth-code")
        return await service.prepare_connection(
            "org_one",
            session.id,
            "user-one",
            "project-one",
            "uumi-automation@project-one.iam.gserviceaccount.com",
        )

    original = await prepare()
    retried = await prepare()

    assert retried == original
    assert repository.session is not None
    assert repository.session.connection_id == original.id


@pytest.mark.anyio
async def test_connector_uses_short_lived_oauth_access_only_for_discovery() -> None:
    oauth_bodies: list[str] = []

    def google_cloud_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            oauth_bodies.append(request.content.decode())
            return httpx.Response(
                200,
                json={"access_token": "temporary-google-token", "expires_in": 3600},
            )
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
                            "email": "uumi-automation@project-one.iam.gserviceaccount.com",
                            "displayName": "Uumi automation",
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
        "https://app.uumi.example/google-cloud/callback",
        SecretManagerConnector(google),
        client,
    )

    discovery = await connector.discover("oauth-code", "pkce-verifier")
    projects = discovery.projects

    encoded = json.dumps(projects)
    assert projects[0]["services"][0]["display_name"] == "worker"
    assert "temporary-google-token" not in encoded
    assert "oauth-client-secret" in oauth_bodies[0]
    discovery.access_token.clear()
    await client.aclose()
    await google.close()


@pytest.mark.anyio
async def test_connector_skips_projects_not_readable_from_the_perimeter() -> None:
    def google_cloud_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200,
                json={"access_token": "temporary-google-token", "expires_in": 3600},
            )
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
                        },
                        {
                            "name": "projects/987654321098",
                            "projectId": "project-outside",
                            "displayName": "Project Outside",
                            "state": "ACTIVE",
                        },
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
                            "spec": {"template": {"spec": {}}},
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
                            "email": "worker@project-one.iam.gserviceaccount.com",
                            "displayName": "Worker",
                        }
                    ]
                },
            )
        if "project-outside" in request.url.path:
            return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})
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
        "https://app.uumi.example/google-cloud/callback",
        SecretManagerConnector(google),
        client,
    )

    discovery = await connector.discover("oauth-code", "pkce-verifier")
    projects = discovery.projects

    assert [project["project_id"] for project in projects] == ["project-one"]
    discovery.access_token.clear()
    await client.aclose()
    await google.close()


@pytest.mark.anyio
async def test_authorization_cipher_binds_token_to_the_onboarding_session() -> None:
    session = GoogleCloudOnboardingSession(
        id="google_one",
        organisation_id="org_one",
        subject="user-one",
        state_hash="a" * 64,
        verifier_hash="b" * 64,
        status=GoogleCloudOnboardingStatus.PENDING,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    kms = Kms()
    cipher = GoogleCloudAuthorizationCipher(
        kms,
        "projects/project-one/locations/us-central1/keyRings/uumi/cryptoKeys/onboarding",
    )
    token = SecretValue(b"temporary-google-token")

    ciphertext, expires_at = await cipher.seal(
        session,
        token,
        NOW + timedelta(hours=1),
    )
    completed = session.model_copy(
        update={
            "status": GoogleCloudOnboardingStatus.COMPLETE,
            "projects": (_project(),),
            "completed_at": NOW,
            "authorization_ciphertext": ciphertext,
            "authorization_expires_at": expires_at,
        }
    )
    opened = await cipher.open(completed, NOW)

    assert opened.bytes() == b"temporary-google-token"
    assert expires_at == session.expires_at
    assert kms.aad
    opened.clear()
    token.clear()


@pytest.mark.anyio
async def test_authorize_preserves_iam_policy_and_is_idempotent() -> None:
    policy: dict[str, object] = {
        "version": 3,
        "etag": "policy-etag",
        "bindings": [
            {"role": "roles/viewer", "members": ["user:owner@example.com"]},
            {
                "role": "roles/iam.serviceAccountTokenCreator",
                "members": ["serviceAccount:conditional@example.iam.gserviceaccount.com"],
                "condition": {
                    "title": "temporary",
                    "expression": "request.time < timestamp('2027-01-01T00:00:00Z')",
                },
            },
        ],
    }
    writes: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal policy
        assert request.headers["Authorization"] == "Bearer temporary-google-token"
        if request.url.path.endswith(":testIamPermissions"):
            if request.url.host == "cloudresourcemanager.googleapis.com":
                return httpx.Response(
                    200,
                    json={
                        "permissions": [
                            "resourcemanager.projects.getIamPolicy",
                            "resourcemanager.projects.setIamPolicy",
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "permissions": [
                        "iam.serviceAccounts.getIamPolicy",
                        "iam.serviceAccounts.setIamPolicy",
                    ]
                },
            )
        if request.url.path.endswith(":getIamPolicy"):
            return httpx.Response(200, json=policy)
        if request.url.path.endswith(":setIamPolicy"):
            body = json.loads(request.content)
            writes.append(body)
            policy = body["policy"]
            return httpx.Response(200, json=policy)
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = GoogleCloudOnboardingConnector(
        "google-client",
        "projects/project-one/secrets/google-oauth/versions/1",
        "https://app.uumi.example/google-cloud/callback",
        object(),  # type: ignore[arg-type]
        client,
    )
    token = SecretValue(b"temporary-google-token")

    for _ in range(2):
        await connector.authorize(
            token,
            "project-one",
            "uumi-automation@project-one.iam.gserviceaccount.com",
            ("worker@project-one.iam.gserviceaccount.com",),
            "uumi-broker@uumi-host.iam.gserviceaccount.com",
            "uumi-api@uumi-host.iam.gserviceaccount.com",
        )

    assert len(writes) == 3
    bindings = cast(dict[str, Any], writes[-1]["policy"])["bindings"]
    assert bindings[0] == {
        "role": "roles/viewer",
        "members": ["user:owner@example.com"],
    }
    assert bindings[1]["condition"]["title"] == "temporary"
    unconditional = {
        binding["role"]: binding["members"] for binding in bindings if "condition" not in binding
    }
    assert unconditional == {
        "roles/viewer": ["user:owner@example.com"],
        "roles/iam.serviceAccountTokenCreator": [
            "serviceAccount:uumi-api@uumi-host.iam.gserviceaccount.com",
            "serviceAccount:uumi-broker@uumi-host.iam.gserviceaccount.com",
        ],
        "roles/iam.serviceAccountUser": [
            "serviceAccount:uumi-automation@project-one.iam.gserviceaccount.com"
        ],
        "roles/run.developer": [
            "serviceAccount:uumi-automation@project-one.iam.gserviceaccount.com"
        ],
        "roles/secretmanager.secretVersionManager": [
            "serviceAccount:uumi-automation@project-one.iam.gserviceaccount.com"
        ],
        "roles/secretmanager.viewer": [
            "serviceAccount:uumi-automation@project-one.iam.gserviceaccount.com"
        ],
    }
    token.clear()
    await client.aclose()


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
        authorization_ciphertext: str,
        authorization_expires_at: datetime,
    ) -> GoogleCloudOnboardingSession:
        self.session = session.model_copy(
            update={
                "status": GoogleCloudOnboardingStatus.COMPLETE,
                "projects": projects,
                "completed_at": completed_at,
                "authorization_ciphertext": authorization_ciphertext,
                "authorization_expires_at": authorization_expires_at,
            }
        )
        return self.session

    async def authorize_session(
        self,
        session: GoogleCloudOnboardingSession,
        authorized_at: datetime,
    ) -> GoogleCloudOnboardingSession:
        assert self.session == session
        self.session = session.model_copy(
            update={
                "authorization_ciphertext": None,
                "authorization_expires_at": None,
                "authorized_at": authorized_at,
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

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        if self.connection is None:
            return ()
        assert self.connection.organisation_id == organisation_id
        return (self.connection,)

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
    def __init__(self) -> None:
        self.authorized = False

    async def discover(self, code: str, verifier: str) -> GoogleCloudDiscovery:
        assert code == "oauth-code"
        assert verifier
        return GoogleCloudDiscovery(
            projects=(
                {
                    "project_id": "project-one",
                    "project_number": "123456789012",
                    "display_name": "Project One",
                    "services": (
                        {
                            "reference": (
                                "projects/project-one/locations/us-central1/services/worker"
                            ),
                            "display_name": "worker",
                            "region": "us-central1",
                            "runtime_identity": "worker@project-one.iam.gserviceaccount.com",
                        },
                    ),
                    "service_accounts": (
                        {
                            "email": "uumi-automation@project-one.iam.gserviceaccount.com",
                            "display_name": "Uumi automation",
                        },
                    ),
                },
            ),
            access_token=SecretValue(b"temporary-google-token"),
            expires_at=NOW + timedelta(minutes=30),
        )

    async def authorize(
        self,
        token: SecretValue,
        project_id: str,
        automation_identity: str,
        runtime_identities: tuple[str, ...],
        broker_service_account: str,
        discovery_service_account: str,
    ) -> None:
        assert token.bytes() == b"temporary-google-token"
        assert project_id == "project-one"
        assert automation_identity == "uumi-automation@project-one.iam.gserviceaccount.com"
        assert runtime_identities == ("worker@project-one.iam.gserviceaccount.com",)
        assert broker_service_account == "uumi-broker@uumi-host.iam.gserviceaccount.com"
        assert discovery_service_account == "uumi-api@uumi-host.iam.gserviceaccount.com"
        self.authorized = True


class Cipher:
    async def seal(
        self,
        session: GoogleCloudOnboardingSession,
        token: SecretValue,
        expires_at: datetime,
    ) -> tuple[str, datetime]:
        assert token.bytes() == b"temporary-google-token"
        return "encrypted-authorization", min(session.expires_at, expires_at)

    async def open(
        self,
        session: GoogleCloudOnboardingSession,
        now: datetime,
    ) -> SecretValue:
        assert session.authorization_ciphertext == "encrypted-authorization"
        assert session.authorization_expires_at is not None
        assert session.authorization_expires_at > now
        return SecretValue(b"temporary-google-token")


class Kms:
    def __init__(self) -> None:
        self.aad = ""
        self.plaintext = ""

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
    ) -> dict[str, object]:
        assert method == "POST"
        assert isinstance(json, dict)
        self.aad = str(json["additionalAuthenticatedData"])
        if url.endswith(":encrypt"):
            self.plaintext = str(json["plaintext"])
            return {"ciphertext": "encrypted-authorization"}
        assert url.endswith(":decrypt")
        assert json["ciphertext"] == "encrypted-authorization"
        return {"plaintext": self.plaintext}


def _project() -> GoogleCloudProject:
    return GoogleCloudProject.model_validate(
        {
            "project_id": "project-one",
            "project_number": "123456789012",
            "display_name": "Project One",
            "services": [
                {
                    "reference": "projects/project-one/locations/us-central1/services/worker",
                    "display_name": "worker",
                    "region": "us-central1",
                }
            ],
            "service_accounts": [
                {
                    "email": "uumi-automation@project-one.iam.gserviceaccount.com",
                    "display_name": "Uumi automation",
                }
            ],
        }
    )
