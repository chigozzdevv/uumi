import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Protocol
from urllib.parse import urlencode

from connectors.googlecloud import GoogleCloudOnboardingConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    GoogleCloudOnboardingSession,
    GoogleCloudOnboardingStatus,
    GoogleCloudProject,
)

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.ids import new_id

_RUNTIME_CAPABILITIES = frozenset(
    {
        "runtime.listServices",
        "runtime.inspectSecretBindings",
        "runtime.deployCandidate",
        "runtime.shiftTraffic",
        "runtime.rollback",
    }
)
_SECRET_CAPABILITIES = frozenset(
    {
        "secretStore.getVersion",
        "secretStore.testConsumerAccess",
        "secretStore.disableVersion",
        "secretStore.destroyVersion",
    }
)


class GoogleCloudRepositoryStore(Protocol):
    async def create_session(
        self, session: GoogleCloudOnboardingSession
    ) -> GoogleCloudOnboardingSession: ...

    async def get_session(
        self, organisation_id: str, session_id: str
    ) -> GoogleCloudOnboardingSession: ...

    async def complete_session(
        self,
        session: GoogleCloudOnboardingSession,
        projects: tuple[GoogleCloudProject, ...],
        completed_at: datetime,
    ) -> GoogleCloudOnboardingSession: ...

    async def attach_connection(
        self,
        session: GoogleCloudOnboardingSession,
        connection_id: str,
    ) -> GoogleCloudOnboardingSession: ...


class GoogleCloudInventoryStore(Protocol):
    async def add_connection(self, value: Connection) -> Connection: ...

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection: ...


class GoogleCloudConnectionValidator(Protocol):
    async def validate(self, connection: Connection) -> None: ...


class GoogleCloudOnboardingService:
    def __init__(
        self,
        repository: GoogleCloudRepositoryStore,
        connector: GoogleCloudOnboardingConnector,
        client_id: str,
        redirect_uri: str,
        clock: Callable[[], datetime],
        inventory: GoogleCloudInventoryStore | None = None,
        validator: GoogleCloudConnectionValidator | None = None,
        broker_service_account: str = "",
    ) -> None:
        self._repository = repository
        self._connector = connector
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._clock = clock
        self._inventory = inventory
        self._validator = validator
        self._broker_service_account = broker_service_account

    async def begin(
        self, organisation_id: str, subject: str
    ) -> tuple[GoogleCloudOnboardingSession, str, str, str]:
        now = self._clock()
        state = token_urlsafe(32)
        verifier = token_urlsafe(64)
        session = GoogleCloudOnboardingSession(
            id=new_id("google"),
            organisation_id=organisation_id,
            subject=subject,
            state_hash=_hash(state),
            verifier_hash=_hash(verifier),
            status=GoogleCloudOnboardingStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        stored = await self._repository.create_session(session)
        authorization_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "access_type": "online",
                "client_id": self._client_id,
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "state": state,
            }
        )
        return stored, state, verifier, authorization_url

    async def complete(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        state: str,
        verifier: str,
        code: str,
    ) -> tuple[GoogleCloudOnboardingSession, tuple[GoogleCloudProject, ...]]:
        session = await self._repository.get_session(organisation_id, session_id)
        now = self._clock()
        _authorise(session, subject, state, verifier, now)
        if session.status is GoogleCloudOnboardingStatus.COMPLETE:
            raise ResourceConflictError("Google Cloud discovery has already been completed")
        metadata = await self._connector.discover(code, verifier)
        projects = tuple(GoogleCloudProject.model_validate(item) for item in metadata)
        if not projects:
            raise ResourceConflictError(
                "Google Cloud returned no project with Cloud Run services "
                "and an automation identity"
            )
        completed = await self._repository.complete_session(session, projects, now)
        return completed, projects

    async def prepare_connection(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        project_id: str,
        automation_identity: str,
    ) -> tuple[Connection, str]:
        inventory = _required(self._inventory, "Google Cloud inventory")
        session = await self._repository.get_session(organisation_id, session_id)
        _authorise_completed(session, subject, self._clock())
        project = next((item for item in session.projects if item.project_id == project_id), None)
        if project is None:
            raise ResourceNotFoundError("Selected Google Cloud project was not discovered")
        account = next(
            (item for item in project.service_accounts if item.email == automation_identity), None
        )
        if account is None:
            raise ResourceNotFoundError("Selected automation identity was not discovered")
        if not project.services:
            raise ResourceConflictError("Selected Google Cloud project has no Cloud Run services")
        if session.connection_id is not None:
            existing = await inventory.get_connection(organisation_id, session.connection_id)
            if (
                existing.authorization_reference != f"workload-identity://{account.email}"
                or not all(
                    resource.startswith(f"projects/{project.project_id}/")
                    for resource in existing.allowed_resources
                )
            ):
                raise ResourceConflictError(
                    "Restart Google Cloud setup to change the selected project or identity"
                )
            return existing, self._grant_command(project.project_id, account.email)
        now = self._clock()
        connection = Connection(
            id=new_id("conn"),
            organisation_id=organisation_id,
            platform="google-cloud",
            display_name=project.display_name,
            roles=frozenset({ConnectionRole.RUNTIME, ConnectionRole.SECRET_STORE}),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference=f"workload-identity://{account.email}",
            capabilities=_RUNTIME_CAPABILITIES | _SECRET_CAPABILITIES,
            allowed_resources=tuple(
                dict.fromkeys(
                    (
                        *(service.reference for service in project.services),
                        f"projects/{project.project_id}/secrets",
                    )
                )
            ),
            status=ConnectionStatus.SETUP_REQUIRED,
            region=project.services[0].region,
            created_at=now,
            updated_at=now,
        )
        stored = await inventory.add_connection(connection)
        try:
            await self._repository.attach_connection(session, stored.id)
        except Exception:
            archived = stored.model_copy(
                update={"archived_at": now, "updated_at": now, "revision": stored.revision + 1}
            )
            await inventory.replace_connection(archived, stored.revision)
            raise
        return stored, self._grant_command(project.project_id, account.email)

    async def verify_connection(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        expected_revision: int,
    ) -> Connection:
        inventory = _required(self._inventory, "Google Cloud inventory")
        validator = _required(self._validator, "Google Cloud access validation")
        session = await self._repository.get_session(organisation_id, session_id)
        _authorise_completed(session, subject, self._clock())
        if session.connection_id is None:
            raise ResourceConflictError("Google Cloud connection has not been prepared")
        connection = await inventory.get_connection(organisation_id, session.connection_id)
        if connection.revision != expected_revision:
            raise ResourceConflictError(
                f"connection expected revision {expected_revision}, found {connection.revision}"
            )
        if connection.status is not ConnectionStatus.SETUP_REQUIRED:
            raise ResourceConflictError("Google Cloud connection is not awaiting access")
        await validator.validate(connection)
        now = self._clock()
        ready = connection.model_copy(
            update={
                "status": ConnectionStatus.READY,
                "authenticated_at": now,
                "last_validated_at": now,
                "updated_at": now,
                "revision": connection.revision + 1,
            }
        )
        return await inventory.replace_connection(ready, expected_revision)

    def _grant_command(self, project_id: str, automation_identity: str) -> str:
        if not self._broker_service_account:
            raise ResourceConflictError("Google Cloud broker identity is unavailable")
        return (
            "gcloud iam service-accounts add-iam-policy-binding "
            f"{automation_identity} --project={project_id} "
            f"--member=serviceAccount:{self._broker_service_account} "
            "--role=roles/iam.serviceAccountTokenCreator"
        )


def _authorise(
    session: GoogleCloudOnboardingSession,
    subject: str,
    state: str,
    verifier: str,
    now: datetime,
) -> None:
    if session.subject != subject:
        raise ResourceConflictError("Google Cloud onboarding belongs to another administrator")
    if session.expires_at <= now:
        raise ResourceConflictError("Google Cloud onboarding session has expired")
    if not hmac.compare_digest(_hash(state), session.state_hash):
        raise ResourceConflictError("Google Cloud onboarding state is invalid")
    if not hmac.compare_digest(_hash(verifier), session.verifier_hash):
        raise ResourceConflictError("Google Cloud onboarding PKCE verifier is invalid")


def _authorise_completed(
    session: GoogleCloudOnboardingSession,
    subject: str,
    now: datetime,
) -> None:
    if session.subject != subject:
        raise ResourceConflictError("Google Cloud onboarding belongs to another administrator")
    if session.expires_at <= now:
        raise ResourceConflictError("Google Cloud onboarding session has expired")
    if session.status is not GoogleCloudOnboardingStatus.COMPLETE:
        raise ResourceConflictError("Google Cloud discovery is incomplete")


def _required[T](value: T | None, label: str) -> T:
    if value is None:
        raise ResourceConflictError(f"{label} is unavailable")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
