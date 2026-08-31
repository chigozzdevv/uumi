import asyncio
import hashlib
import hmac
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Protocol, TypeVar

import httpx
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionStatus,
    ConnectionWaiter,
    Contract,
    PageCheckpoint,
    PlaybookVersion,
    ResumeRunCommand,
    RotationRun,
    RunStatus,
    SetupSession,
    SetupStatus,
)
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.paths import FirestorePaths
from pydantic import Field

from browser.auth import is_domain_pattern
from browser.compute import BrowserVm

T = TypeVar("T", bound=Contract)

_SETUP_MINUTES = 30
_READINESS_PROBE_TIMEOUT_SECONDS = 2.0
_PROVISIONING_TIMEOUT = timedelta(minutes=5)


class SetupAuthenticationRequiredError(ResourceConflictError):
    pass


class ProviderSessionValidation(Contract):
    url: str = Field(min_length=1, max_length=2048)
    login_url_pattern: str = Field(min_length=1, max_length=1024)
    checkpoint: PageCheckpoint


class SetupCatalog(Protocol):
    async def create(self, path: str, value: Contract) -> None: ...

    async def get(self, path: str, model: type[T]) -> T: ...

    async def replace(
        self,
        path: str,
        model: type[T],
        expected_revision: int,
        update: Callable[[T], T],
    ) -> T: ...

    async def list(self, path: str, model: type[T], limit: int = 200) -> tuple[T, ...]: ...


class SetupConnections(Protocol):
    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

    async def get_playbook_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion: ...

    async def complete_setup(
        self,
        current_session: SetupSession,
        changed_session: SetupSession,
        current_connection: Connection,
        changed_connection: Connection,
    ) -> tuple[SetupSession, Connection]: ...

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection: ...


class SetupVms(Protocol):
    async def create(
        self,
        organisation_id: str,
        session_id: str,
        expires_at: datetime,
        setup_token_hash: str | None = None,
        allowed_domains: tuple[str, ...] = (),
        storage_domains: tuple[str, ...] = (),
        secret_container: str | None = None,
    ) -> BrowserVm: ...

    async def delete(self, instance: str) -> None: ...

    async def exists(self, instance: str) -> bool: ...

    async def reconcile(self) -> None: ...


class SetupSecrets(Protocol):
    async def versions(self, secret: str) -> tuple[dict[str, Any], ...]: ...

    async def disable(self, version: str) -> dict[str, Any]: ...


class WaitingRunResumer(Protocol):
    async def resume(
        self,
        organisation_id: str,
        run_ids: tuple[str, ...],
        actor_id: str,
    ) -> tuple[str, ...]: ...


class BrowserSetupApi(Protocol):
    @property
    def setup_url(self) -> str: ...

    @property
    def gateway_url(self) -> str: ...

    async def begin(
        self,
        organisation_id: str,
        connection_id: str,
        subject: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[SetupSession, str]: ...

    async def complete(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        token: str,
        subject: str,
        actor_id: str | None = None,
    ) -> tuple[SetupSession, Connection, tuple[str, ...]]: ...

    async def abort(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        subject: str,
    ) -> SetupSession: ...

    async def get(self, organisation_id: str, setup_id: str) -> SetupSession: ...

    async def reap_expired(self, organisation_id: str) -> tuple[SetupSession, ...]: ...


class BrowserSetupService:
    def __init__(
        self,
        catalog: SetupCatalog,
        connections: SetupConnections,
        vms: SetupVms,
        secrets: SetupSecrets,
        setup_url: str,
        gateway_url: str,
        session_project: str,
        clock: Callable[[], datetime],
        http: httpx.AsyncClient | None = None,
        runs: WaitingRunResumer | None = None,
    ) -> None:
        self._catalog = catalog
        self._connections = connections
        self._vms = vms
        self._secrets = secrets
        self._setup_url = setup_url.rstrip("/")
        self._gateway_url = gateway_url.rstrip("/")
        self._session_project = session_project
        self._clock = clock
        self._http = http or httpx.AsyncClient(timeout=60)
        self._runs = runs

    @property
    def setup_url(self) -> str:
        return self._setup_url

    @property
    def gateway_url(self) -> str:
        return self._gateway_url

    async def begin(
        self,
        organisation_id: str,
        connection_id: str,
        subject: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[SetupSession, str]:
        connection = await self._connections.get_connection(organisation_id, connection_id)
        if (
            connection.interface is not ConnectionInterface.BROWSER
            or connection.authorization is not ConnectionAuthorization.BROWSER_SESSION
        ):
            raise ResourceConflictError("browser setup requires a browser connection")
        if connection.playbook_id is None or connection.playbook_version_id is None:
            raise ResourceConflictError(
                "attach a published playbook before opening the secure browser"
            )
        secret_container = (
            f"projects/{self._session_project}/secrets/uumi-browser-session-{organisation_id}"
        )
        await self._require_secret(secret_container)
        domains = connection.allowed_resources
        if not domains or any(not is_domain_pattern(value) for value in domains):
            raise ResourceConflictError("browser connection must declare allowed domains")
        if any(not is_domain_pattern(value) for value in extra_domains):
            raise ResourceConflictError("setup extra domains are invalid")
        vm_domains = tuple(dict.fromkeys((*domains, *extra_domains)))
        now = self._clock()
        token = token_urlsafe(32)
        session = SetupSession(
            id=_setup_id(connection_id),
            organisation_id=organisation_id,
            connection_id=connection_id,
            secret_container=secret_container,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            subject=subject,
            allowed_domains=vm_domains,
            status=SetupStatus.PROVISIONING,
            created_at=now,
            expires_at=now + timedelta(minutes=_SETUP_MINUTES),
            updated_at=now,
        )
        path = FirestorePaths.setup(organisation_id, session.id)
        session = await self._replace_or_create(path, session)
        created: BrowserVm | None = None
        try:
            created = await self._vms.create(
                organisation_id,
                session.id,
                session.expires_at,
                setup_token_hash=session.token_hash,
                allowed_domains=vm_domains,
                storage_domains=domains,
                secret_container=secret_container,
            )
            instance = created.instance
            address = created.internal_address
            session = await self._catalog.replace(
                path,
                SetupSession,
                session.revision,
                lambda current: current.model_copy(
                    update={
                        "worker_instance": instance,
                        "internal_address": address,
                        "updated_at": self._clock(),
                        "revision": current.revision + 1,
                    }
                ),
            )
            return session, token
        except asyncio.CancelledError:
            await asyncio.shield(self._terminate_failed_begin(path, session, created))
            raise
        except Exception:
            await self._terminate_failed_begin(path, session, created)
            raise

    async def complete(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        token: str,
        subject: str,
        actor_id: str | None = None,
    ) -> tuple[SetupSession, Connection, tuple[str, ...]]:
        path = FirestorePaths.setup(organisation_id, setup_id)
        session = await self._catalog.get(path, SetupSession)
        _owner(session, subject)
        _token(session, token)
        if session.status is SetupStatus.COMPLETE:
            connection = await self._connections.get_connection(
                organisation_id, session.connection_id
            )
            try:
                resumed = await self._resume_waiting(
                    organisation_id, session.connection_id, actor_id or subject
                )
            finally:
                await self._delete_vm(session)
            return session, connection, resumed
        if session.status is not SetupStatus.READY:
            raise ResourceConflictError("setup session is already being captured")
        if session.expires_at <= self._clock():
            raise ResourceConflictError("setup session has expired")
        session = await self._catalog.replace(
            path,
            SetupSession,
            expected_revision,
            lambda current: current.model_copy(
                update={
                    "status": SetupStatus.CAPTURING,
                    "updated_at": self._clock(),
                    "revision": current.revision + 1,
                }
            ),
        )
        before: frozenset[str] = frozenset()
        baseline_loaded = False
        auth_reference: str | None = None
        previous_connection: Connection | None = None
        updated_connection: Connection | None = None
        expected_session: SetupSession | None = None
        expected_connection: Connection | None = None
        try:
            before = _version_names(await self._secrets.versions(session.secret_container))
            baseline_loaded = True
            previous_connection = await self._connections.get_connection(
                organisation_id, session.connection_id
            )
            validation = await self._session_validation(previous_connection)
            result = await self._store(session, token, validation)
            auth_reference = _canonical_secret_reference(
                session.secret_container, result.get("secret_reference")
            )
            if auth_reference is None:
                raise ResourceConflictError("setup worker returned no secret version reference")
            completed_at = self._clock()
            expected_connection = previous_connection.model_copy(
                update={
                    "authorization_reference": auth_reference,
                    "status": ConnectionStatus.READY,
                    "authenticated_at": completed_at,
                    "last_validated_at": completed_at,
                    "updated_at": completed_at,
                    "revision": previous_connection.revision + 1,
                }
            )
            expected_session = session.model_copy(
                update={
                    "status": SetupStatus.COMPLETE,
                    "auth_reference": auth_reference,
                    "updated_at": completed_at,
                    "revision": session.revision + 1,
                }
            )
            session, updated_connection = await self._connections.complete_setup(
                session,
                expected_session,
                previous_connection,
                expected_connection,
            )
        except Exception as error:
            committed = await self._committed_setup(
                path,
                expected_session,
                expected_connection,
            )
            if committed is not None:
                session, updated_connection = committed
            else:
                cleanup_error = (
                    await self._reconcile_store(session.secret_container, before, auth_reference)
                    if baseline_loaded
                    else None
                )
                await self._terminate_failed(path, session)
                await self._delete_vm(session)
                if isinstance(error, SetupAuthenticationRequiredError):
                    await self._mark_reauthentication(organisation_id, session.connection_id)
                if cleanup_error is not None:
                    raise cleanup_error from error
                raise
        try:
            resumed = await self._resume_waiting(
                organisation_id, session.connection_id, actor_id or subject
            )
        finally:
            await self._delete_vm(session)
        assert updated_connection is not None
        return session, updated_connection, resumed

    async def abort(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        subject: str,
    ) -> SetupSession:
        path = FirestorePaths.setup(organisation_id, setup_id)
        session = await self._catalog.get(path, SetupSession)
        _owner(session, subject)
        if session.status in {SetupStatus.COMPLETE, SetupStatus.TERMINATED}:
            raise ResourceConflictError("setup session is already finished")
        session = await self._catalog.replace(
            path,
            SetupSession,
            expected_revision,
            lambda current: current.model_copy(
                update={
                    "status": SetupStatus.TERMINATED,
                    "terminated_at": self._clock(),
                    "updated_at": self._clock(),
                    "revision": current.revision + 1,
                }
            ),
        )
        await self._delete_vm(session)
        return session

    async def get(self, organisation_id: str, setup_id: str) -> SetupSession:
        path = FirestorePaths.setup(organisation_id, setup_id)
        session = await self._catalog.get(path, SetupSession)
        if self._must_terminate(session):
            return await self._terminate(path, session)
        if session.status is not SetupStatus.PROVISIONING or session.internal_address is None:
            return session
        if not await self._is_ready(session):
            return session
        try:
            return await self._catalog.replace(
                path,
                SetupSession,
                session.revision,
                lambda current: current.model_copy(
                    update={
                        "status": SetupStatus.READY,
                        "updated_at": self._clock(),
                        "revision": current.revision + 1,
                    }
                ),
            )
        except ResourceConflictError:
            return await self._catalog.get(path, SetupSession)

    async def reap_expired(self, organisation_id: str) -> tuple[SetupSession, ...]:
        path = f"{FirestorePaths.organisation(organisation_id)}/setups"
        sessions = await self._catalog.list(path, SetupSession)
        terminated: list[SetupSession] = []
        for session in sessions:
            if not self._must_terminate(session):
                continue
            ended = await self._terminate(
                FirestorePaths.setup(organisation_id, session.id), session
            )
            if ended.status is SetupStatus.TERMINATED:
                terminated.append(ended)
        await self._vms.reconcile()
        return tuple(terminated)

    def _must_terminate(self, session: SetupSession) -> bool:
        now = self._clock()
        if session.status not in {
            SetupStatus.PROVISIONING,
            SetupStatus.READY,
            SetupStatus.CAPTURING,
        }:
            return False
        if session.expires_at <= now:
            return True
        return (
            session.status is SetupStatus.PROVISIONING
            and session.created_at + _PROVISIONING_TIMEOUT <= now
        )

    async def _terminate(self, path: str, session: SetupSession) -> SetupSession:
        try:
            expired = await self._catalog.replace(
                path,
                SetupSession,
                session.revision,
                lambda current: current.model_copy(
                    update={
                        "status": SetupStatus.TERMINATED,
                        "terminated_at": self._clock(),
                        "updated_at": self._clock(),
                        "revision": current.revision + 1,
                    }
                ),
            )
        except ResourceConflictError:
            return await self._catalog.get(path, SetupSession)
        with suppress(Exception):
            await self._delete_vm(expired)
        return expired

    async def _replace_or_create(self, path: str, session: SetupSession) -> SetupSession:
        try:
            existing = await self._catalog.get(path, SetupSession)
        except ResourceNotFoundError:
            await self._catalog.create(path, session)
            return session
        active = existing.status in {
            SetupStatus.PROVISIONING,
            SetupStatus.READY,
            SetupStatus.CAPTURING,
        } and (existing.expires_at > session.created_at)
        if active and (
            existing.worker_instance is None or await self._vms.exists(existing.worker_instance)
        ):
            raise ResourceConflictError("a setup session is already active for this connection")
        if existing.worker_instance is not None:
            await self._vms.delete(existing.worker_instance)
        return await self._catalog.replace(
            path,
            SetupSession,
            existing.revision,
            lambda current: session.model_copy(update={"revision": current.revision + 1}),
        )

    async def _terminate_failed_begin(
        self,
        path: str,
        session: SetupSession,
        created: BrowserVm | None,
    ) -> None:
        if created is not None:
            with suppress(Exception):
                await self._vms.delete(created.instance)
        with suppress(Exception):
            await self._catalog.replace(
                path,
                SetupSession,
                session.revision,
                lambda current: current.model_copy(
                    update={
                        "status": SetupStatus.TERMINATED,
                        "terminated_at": self._clock(),
                        "updated_at": self._clock(),
                        "revision": current.revision + 1,
                    }
                ),
            )

    async def _require_secret(self, secret_container: str) -> None:
        try:
            await self._secrets.versions(secret_container)
        except Exception as error:
            raise ResourceConflictError("secret container is not writable") from error

    async def _resume_waiting(
        self,
        organisation_id: str,
        connection_id: str,
        actor_id: str,
    ) -> tuple[str, ...]:
        path = FirestorePaths.connection_waiter(organisation_id, connection_id)
        try:
            waiter = await self._catalog.get(path, ConnectionWaiter)
        except ResourceNotFoundError:
            return ()
        resumed: tuple[str, ...] = ()
        if self._runs is not None and waiter.run_ids:
            resumed = await self._runs.resume(organisation_id, waiter.run_ids, actor_id)
        resumed_set = frozenset(resumed)
        remaining = tuple(run_id for run_id in waiter.run_ids if run_id not in resumed_set)
        if remaining != waiter.run_ids:
            await self._catalog.replace(
                path,
                ConnectionWaiter,
                waiter.revision,
                lambda current: current.model_copy(
                    update={"run_ids": remaining, "revision": current.revision + 1}
                ),
            )
        return resumed

    async def _store(
        self,
        session: SetupSession,
        token: str,
        validation: ProviderSessionValidation,
    ) -> dict[str, Any]:
        if session.internal_address is None:
            raise ResourceConflictError("setup worker has no internal address")
        try:
            response = await self._http.post(
                f"http://{session.internal_address}:8080/v1/setup/store",
                headers={"X-Uumi-Setup": token},
                json=validation.model_dump(mode="json"),
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise ResourceConflictError("setup worker was unavailable") from None
        if response.status_code == 403:
            raise ResourceConflictError("setup worker rejected the token")
        if response.status_code == 409:
            try:
                body = response.json()
            except ValueError:
                body = None
            code = body.get("code") if isinstance(body, dict) else None
            message = body.get("message") if isinstance(body, dict) else None
            reason = (
                message
                if isinstance(message, str) and message
                else "no provider session was captured on the connection domains"
            )
            if code == "authentication-required":
                raise SetupAuthenticationRequiredError(reason)
            raise ResourceConflictError(reason)
        if response.status_code != 200:
            raise ResourceConflictError(f"setup worker returned HTTP {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise ResourceConflictError("setup worker returned invalid metadata")
        return body

    async def _session_validation(self, connection: Connection) -> ProviderSessionValidation:
        if connection.playbook_id is None or connection.playbook_version_id is None:
            raise ResourceConflictError("browser connection has no published playbook")
        version = await self._connections.get_playbook_version(
            connection.organisation_id,
            connection.playbook_id,
            connection.playbook_version_id,
        )
        login_url_pattern = version.definition.login_url_pattern
        if login_url_pattern is None:
            raise ResourceConflictError("published browser playbook has no login boundary")
        for step in version.definition.steps:
            if step.checkpoint is None:
                continue
            url = _validation_url(step.operation, step.parameters, step.checkpoint)
            if url is not None:
                return ProviderSessionValidation(
                    url=url,
                    login_url_pattern=login_url_pattern,
                    checkpoint=step.checkpoint,
                )
        raise ResourceConflictError("published browser playbook has no post-login validation route")

    async def _mark_reauthentication(
        self,
        organisation_id: str,
        connection_id: str,
    ) -> None:
        for _ in range(3):
            current = await self._connections.get_connection(organisation_id, connection_id)
            try:
                await self._connections.update_authentication(
                    organisation_id,
                    connection_id,
                    current.revision,
                    None,
                    ConnectionStatus.REAUTHENTICATION,
                    self._clock(),
                )
                return
            except ResourceConflictError:
                continue

    async def _reconcile_store(
        self,
        secret: str,
        before: frozenset[str],
        known_version: str | None,
    ) -> ResourceConflictError | None:
        if known_version is not None:
            try:
                await self._secrets.disable(known_version)
            except Exception:
                return ResourceConflictError(
                    "setup failed and its secret version requires manual disablement"
                )
            return None
        try:
            candidates = [
                item["name"]
                for item in await self._secrets.versions(secret)
                if isinstance(item.get("name"), str)
                and item["name"] not in before
                and item.get("state") == "ENABLED"
            ]
        except Exception:
            return ResourceConflictError(
                "setup outcome is ambiguous and secret versions could not be reconciled"
            )
        if len(candidates) > 1:
            return ResourceConflictError(
                "setup outcome is ambiguous and multiple secret versions require review"
            )
        if candidates:
            try:
                await self._secrets.disable(candidates[0])
            except Exception:
                return ResourceConflictError(
                    "setup failed and its secret version requires manual disablement"
                )
        return None

    async def _terminate_failed(self, path: str, session: SetupSession) -> None:
        with suppress(Exception):
            await self._catalog.replace(
                path,
                SetupSession,
                session.revision,
                lambda current: current.model_copy(
                    update={
                        "status": SetupStatus.TERMINATED,
                        "terminated_at": self._clock(),
                        "updated_at": self._clock(),
                        "revision": current.revision + 1,
                    }
                ),
            )

    async def _committed_setup(
        self,
        path: str,
        expected_session: SetupSession | None,
        expected_connection: Connection | None,
    ) -> tuple[SetupSession, Connection] | None:
        if expected_session is None or expected_connection is None:
            return None
        try:
            stored_session = await self._catalog.get(path, SetupSession)
            stored_connection = await self._connections.get_connection(
                expected_connection.organisation_id, expected_connection.id
            )
        except Exception:
            return None
        if stored_session == expected_session and stored_connection == expected_connection:
            return stored_session, stored_connection
        return None

    async def _is_ready(self, session: SetupSession) -> bool:
        if session.internal_address is None:
            return False
        try:
            response = await self._http.get(
                f"http://{session.internal_address}:8080/health/live",
                timeout=_READINESS_PROBE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def _delete_vm(self, session: SetupSession) -> None:
        if session.worker_instance is None:
            return
        await self._vms.delete(session.worker_instance)


def _owner(session: SetupSession, subject: str) -> None:
    if session.subject != subject:
        raise ResourceConflictError("setup session belongs to another operator")


def _token(session: SetupSession, token: str) -> None:
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), session.token_hash):
        raise ResourceConflictError("setup token is invalid")


def _setup_id(connection_id: str) -> str:
    suffix = connection_id.removeprefix("connection_")
    candidate = f"setup_{suffix}"
    if 3 <= len(candidate) <= 128 and candidate[0].isalpha():
        return candidate
    digest = hashlib.sha256(connection_id.encode()).hexdigest()[:40]
    return f"setup_{digest}"


def _version_names(values: tuple[dict[str, Any], ...]) -> frozenset[str]:
    return frozenset(item["name"] for item in values if isinstance(item.get("name"), str))


def _canonical_secret_reference(container: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    expected_project, separator, expected_secret = container.partition("/secrets/")
    candidate_project, candidate_separator, candidate_secret_and_version = value.partition(
        "/secrets/"
    )
    if not separator or not candidate_separator:
        return None
    if candidate_project != expected_project and (
        not candidate_project.startswith("projects/")
        or not candidate_project.removeprefix("projects/").isdigit()
    ):
        return None
    secret, version_separator, version = candidate_secret_and_version.partition("/versions/")
    if (
        secret != expected_secret
        or not version_separator
        or re.fullmatch(r"[1-9][0-9]{0,18}", version) is None
    ):
        return None
    return f"{container}/versions/{version}"


def _validation_url(
    operation: str,
    parameters: dict[str, Any],
    checkpoint: PageCheckpoint,
) -> str | None:
    value = parameters.get("url")
    if operation == "navigate" and isinstance(value, str) and value:
        return value
    pattern = checkpoint.url_pattern
    if "*" not in pattern:
        return pattern
    if pattern.endswith("*") and pattern.count("*") == 1:
        return pattern.removesuffix("*")
    return None


class WorkflowRunResumer:
    def __init__(self, workflow: Any, clock: Callable[[], datetime]) -> None:
        self._workflow = workflow
        self._clock = clock

    async def resume(
        self,
        organisation_id: str,
        run_ids: tuple[str, ...],
        actor_id: str,
    ) -> tuple[str, ...]:
        resumed: list[str] = []
        expires_at = self._clock() + timedelta(minutes=30)
        for run_id in run_ids:
            try:
                run = await self._workflow.get(organisation_id, run_id)
            except Exception:
                continue
            if not isinstance(run, RotationRun) or run.status is not RunStatus.PAUSED:
                continue
            try:
                await self._workflow.resume(
                    ResumeRunCommand(
                        id=_resume_command_id(organisation_id, run_id),
                        organisation_id=organisation_id,
                        run_id=run_id,
                        actor_id=actor_id,
                        expected_revision=run.revision,
                        owner_id=actor_id,
                        expires_at=expires_at,
                    )
                )
            except Exception:
                continue
            resumed.append(run_id)
        return tuple(resumed)


def _resume_command_id(organisation_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{organisation_id}\0{run_id}\0reauth-resume".encode()).hexdigest()
    return f"cmd_{digest[:40]}"
