import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Protocol
from urllib.parse import urlencode

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
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
from core.googlecloud.authorization import GoogleCloudAuthorizationCipher
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
        authorization_ciphertext: str,
        authorization_expires_at: datetime,
    ) -> GoogleCloudOnboardingSession: ...

    async def attach_connection(
        self,
        session: GoogleCloudOnboardingSession,
        connection_id: str,
    ) -> GoogleCloudOnboardingSession: ...

    async def authorize_session(
        self,
        session: GoogleCloudOnboardingSession,
        authorized_at: datetime,
    ) -> GoogleCloudOnboardingSession: ...


class GoogleCloudInventoryStore(Protocol):
    async def add_connection(self, value: Connection) -> Connection: ...

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]: ...

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
        authorization_cipher: GoogleCloudAuthorizationCipher | None = None,
        discovery_service_account: str = "",
    ) -> None:
        self._repository = repository
        self._connector = connector
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._clock = clock
        self._inventory = inventory
        self._validator = validator
        self._broker_service_account = broker_service_account
        self._authorization_cipher = authorization_cipher
        self._discovery_service_account = discovery_service_account

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
        cipher = _required(self._authorization_cipher, "Google Cloud authorization protection")
        try:
            discovery = await self._connector.discover(code, verifier)
            try:
                projects = tuple(
                    GoogleCloudProject.model_validate(item) for item in discovery.projects
                )
                if not projects:
                    raise ResourceConflictError(
                        "Google Cloud returned no project with Cloud Run services "
                        "and an automation identity"
                    )
                ciphertext, authorization_expires_at = await cipher.seal(
                    session,
                    discovery.access_token,
                    discovery.expires_at,
                )
                completed = await self._repository.complete_session(
                    session,
                    projects,
                    now,
                    ciphertext,
                    authorization_expires_at,
                )
                return completed, projects
            finally:
                discovery.access_token.clear()
        except ConnectorError as error:
            raise ResourceConflictError(str(error)) from None

    async def prepare_connection(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        project_id: str,
        automation_identity: str,
    ) -> Connection:
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
            return existing
        authorization_reference = f"workload-identity://{account.email}"
        reusable = next(
            (
                item
                for item in await inventory.connections(organisation_id)
                if item.platform == "google-cloud"
                and item.status is ConnectionStatus.SETUP_REQUIRED
                and item.archived_at is None
                and item.authorization_reference == authorization_reference
                and item.allowed_resources
                and all(
                    resource.startswith(f"projects/{project.project_id}/")
                    for resource in item.allowed_resources
                )
            ),
            None,
        )
        if reusable is not None:
            await self._repository.attach_connection(session, reusable.id)
            return reusable
        now = self._clock()
        connection = Connection(
            id=new_id("conn"),
            organisation_id=organisation_id,
            platform="google-cloud",
            display_name=project.display_name,
            roles=frozenset({ConnectionRole.RUNTIME, ConnectionRole.SECRET_STORE}),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference=authorization_reference,
            capabilities=_RUNTIME_CAPABILITIES | _SECRET_CAPABILITIES,
            allowed_resources=tuple(
                dict.fromkeys(
                    (
                        *(
                            f"projects/{project.project_id}/locations/{region}/services"
                            for region in dict.fromkeys(
                                service.region for service in project.services
                            )
                        ),
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
        return stored

    async def authorize_connection(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        expected_revision: int,
    ) -> Connection:
        inventory = _required(self._inventory, "Google Cloud inventory")
        validator = _required(self._validator, "Google Cloud access validation")
        cipher = _required(self._authorization_cipher, "Google Cloud authorization protection")
        session = await self._repository.get_session(organisation_id, session_id)
        _authorise_completed(session, subject, self._clock())
        if session.connection_id is None:
            raise ResourceConflictError("Google Cloud connection has not been prepared")
        connection = await inventory.get_connection(organisation_id, session.connection_id)
        if connection.status is ConnectionStatus.READY:
            if session.authorization_ciphertext is not None:
                await self._repository.authorize_session(session, self._clock())
            return connection
        if connection.revision != expected_revision:
            raise ResourceConflictError(
                f"connection expected revision {expected_revision}, found {connection.revision}"
            )
        if connection.status is not ConnectionStatus.SETUP_REQUIRED:
            raise ResourceConflictError("Google Cloud connection is not awaiting access")
        if not self._broker_service_account or not self._discovery_service_account:
            raise ResourceConflictError("Google Cloud service identities are unavailable")
        project, automation_identity = _selection(session, connection)
        token: SecretValue | None = None
        try:
            token = await cipher.open(session, self._clock())
            await self._connector.authorize(
                token,
                project.project_id,
                automation_identity,
                tuple(
                    dict.fromkeys(
                        service.runtime_identity
                        for service in project.services
                        if service.runtime_identity is not None
                    )
                ),
                self._broker_service_account,
                self._discovery_service_account,
            )
            await validator.validate(connection)
        except ConnectorError as error:
            raise ResourceConflictError(str(error)) from None
        finally:
            if token is not None:
                token.clear()
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
        stored = await inventory.replace_connection(ready, expected_revision)
        await self._repository.authorize_session(session, now)
        return stored


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


def _selection(
    session: GoogleCloudOnboardingSession,
    connection: Connection,
) -> tuple[GoogleCloudProject, str]:
    reference = connection.authorization_reference or ""
    automation_identity = reference.removeprefix("workload-identity://")
    for project in session.projects:
        if not any(
            resource.startswith(f"projects/{project.project_id}/")
            for resource in connection.allowed_resources
        ):
            continue
        if any(account.email == automation_identity for account in project.service_accounts):
            return project, automation_identity
    raise ResourceConflictError("Google Cloud connection selection is invalid")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
