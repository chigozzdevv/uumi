import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from broker import CapabilitySigner
from browser.access import BrowserAccessService
from browser.auth import BrowserAuthBroker
from browser.compute import BrowserVm, BrowserVmManager
from browser.driver import AuthenticationRequiredError, BrowserDriver, metadata_url
from browser.gateway import BrowserSessionGateway
from browser.model import ComputerUseClient
from browser.service import BrowserService
from browser.setup import BrowserSetupService
from browser.worker import ComputerUseWorker
from connectors.base.errors import ConnectorError
from contracts import (
    BrowserAccessMode,
    BrowserAction,
    BrowserActionKind,
    BrowserActionRecord,
    BrowserActionStatus,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConnectionWaiter,
    ExecutionMethod,
    NotificationKind,
    PageCheckpoint,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookState,
    PlaybookStep,
    PlaybookVersion,
    RecoveryAction,
    RecoveryBranch,
    ReplayCheckpoint,
    RotationRun,
    RunStatus,
    SecureCaptureResult,
    SecureField,
    Selector,
    SelectorKind,
    SetupSession,
    SetupStatus,
    Stage,
    Trigger,
)
from coordinator.browser import BrowserPauseError, BrowserStepExecutor
from coordinator.service import _flag_reauthentication
from core.auth import AccessControl, AuthenticatedIdentity, PrincipalGrant, Role
from core.errors import CapabilityError, ResourceConflictError, ResourceNotFoundError
from core.storage.paths import FirestorePaths
from testkit import make_http_provider_api, make_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Repository:
    def __init__(self) -> None:
        self.session: BrowserSession | None = None
        self.actions: dict[str, BrowserActionRecord] = {}
        self.captures: list[SecureCaptureResult] = []

    async def create(self, session: BrowserSession) -> BrowserSession:
        self.session = session
        return session

    async def get(self, organisation_id: str, session_id: str) -> BrowserSession:
        assert self.session is not None
        return self.session

    async def update(
        self,
        organisation_id: str,
        session_id: str,
        expected_revision: int,
        changed: BrowserSession,
    ) -> BrowserSession:
        assert self.session is not None and self.session.revision == expected_revision
        self.session = changed
        return changed

    async def save_capture(self, result: SecureCaptureResult) -> SecureCaptureResult:
        self.captures.append(result)
        return result

    async def complete_capture(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        result: SecureCaptureResult,
    ) -> BrowserSession:
        assert self.session == current
        self.captures.append(result)
        self.session = changed
        return changed

    async def save_checkpoint(self, checkpoint: ReplayCheckpoint) -> ReplayCheckpoint:
        return checkpoint

    async def begin_action(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        action: BrowserAction,
        authorised_at: datetime,
    ) -> BrowserSession:
        self.actions[action.id] = BrowserActionRecord(
            id=action.id,
            organisation_id=current.organisation_id,
            session_id=current.id,
            action=action,
            status=BrowserActionStatus.AUTHORIZED,
            authorised_at=authorised_at,
        )
        self.session = changed
        return changed

    async def finish_action(
        self,
        organisation_id: str,
        session_id: str,
        action_id: str,
        status: BrowserActionStatus,
        error: str | None,
        completed_at: datetime,
    ) -> BrowserActionRecord:
        current = self.actions[action_id]
        changed = current.model_copy(
            update={"status": status, "error": error, "completed_at": completed_at}
        )
        self.actions[action_id] = changed
        return changed


@pytest.mark.anyio
async def test_secure_capture_barriers_remain_until_result_is_persisted() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    session = await service.arm_capture("org_one", session.id, session.revision)

    assert session.status is BrowserStatus.CAPTURING
    assert session.model_paused is True
    assert session.recording_paused is True

    result = SecureCaptureResult(
        id="capture_one",
        organisation_id="org_one",
        session_id=session.id,
        field_name="api_key",
        provider_id="provider-key-one",
        secret_reference="projects/project-one/secrets/key/versions/4",
        fingerprint="a" * 64,
        masked_value_digest="b" * 64,
        captured_at=NOW,
    )
    session = await service.complete_capture(result, session.revision)

    assert repository.captures == [result]
    assert session.status is BrowserStatus.RUNNING
    assert session.model_paused is False
    assert session.recording_paused is False


@pytest.mark.anyio
async def test_stale_browser_action_is_rejected_before_authorisation() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.CLICK,
        selector=Selector(kind=SelectorKind.ROLE, value="button", name="Continue"),
        fencing_token=9,
    )

    with pytest.raises(ResourceConflictError, match="stale"):
        await service.authorize_action("org_one", session.id, session.revision, action)

    assert repository.actions == {}


@pytest.mark.anyio
async def test_browser_input_value_is_not_persisted_in_action_history() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.TYPE,
        selector=Selector(kind=SelectorKind.LABEL, value="Account name"),
        value="operator-supplied-value",
        fencing_token=session.fencing_token,
    )

    await service.authorize_action("org_one", session.id, session.revision, action)

    assert repository.actions[action.id].action.value == "<redacted>"


@pytest.mark.anyio
async def test_browser_navigation_query_and_fragment_are_not_persisted() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.start("org_one", session.id, session.revision)
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.NAVIGATE,
        url="https://console.vendor.example.com/callback?code=secret#token",
        fencing_token=session.fencing_token,
    )

    await service.authorize_action("org_one", session.id, session.revision, action)

    assert repository.actions[action.id].action.url == (
        "https://console.vendor.example.com/callback"
    )


def test_browser_metadata_url_keeps_only_origin_and_path() -> None:
    assert metadata_url("https://vendor.example/callback?code=secret#fragment") == (
        "https://vendor.example/callback"
    )


@pytest.mark.anyio
async def test_takeover_capability_is_identity_bound_and_releases_to_a_safe_pause() -> None:
    repository = Repository()
    sessions = BrowserService(repository, lambda: NOW)
    session = await sessions.create(_session())
    session = await sessions.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await sessions.start("org_one", session.id, session.revision)
    session = await sessions.freeze("org_one", session.id, session.revision)
    run = RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="credential_one",
        trigger=Trigger(
            source="incident",
            event_id="event_one",
            actor_id="actor_one",
            reason="credential exposure",
            urgency="high",
            received_at=NOW,
        ),
        policy_version="policy_one",
        status=RunStatus.PAUSED,
        created_at=NOW,
        updated_at=NOW,
    )
    catalog = Catalog(repository, run)
    signer = CapabilitySigner(b"x" * 32)

    async def load_signer() -> CapabilitySigner:
        return signer

    access = BrowserAccessService(
        catalog,
        sessions,
        load_signer,
        "https://browser.example.com",
        lambda: NOW,
    )
    grant = await access.issue(
        "org_one", session.id, BrowserAccessMode.TAKEOVER, "operator-subject"
    )
    claims = signer.verify(grant.capability, NOW)

    assert grant.session.status is BrowserStatus.TAKEOVER
    assert grant.session.takeover_subject == "operator-subject"
    assert grant.session.recording_paused is True
    assert claims.tool == "browser.takeover"
    assert claims.agent_id.startswith("actor_")

    released = await access.release("org_one", session.id, "operator-subject")
    rebound = await sessions.rebind_fence("org_one", session.id, released.revision, 4)

    assert released.status is BrowserStatus.PAUSED
    assert released.recording_paused is True
    assert rebound.fencing_token == 4


@pytest.mark.anyio
async def test_takeover_blocks_an_alternate_selector_for_a_protected_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from browser import workerapp

    protected = Selector(kind=SelectorKind.TEST_ID, value="revoke")
    version = _computer_version()
    revoke = next(step for step in version.definition.steps if step.stage is Stage.REVOKE)
    version = version.model_copy(
        update={
            "definition": version.definition.model_copy(
                update={
                    "steps": tuple(
                        step.model_copy(update={"selectors": (protected,)})
                        if step.id == revoke.id
                        else step
                        for step in version.definition.steps
                    )
                }
            )
        }
    )

    class VersionCatalog:
        async def get(self, path: str, model: type[Any]) -> Any:
            del path, model
            return version

    class ElementDriver:
        async def same_element(self, left: Selector, right: Selector) -> bool:
            return left.kind is SelectorKind.CSS and right == protected

    monkeypatch.setattr(workerapp, "FirestoreCatalog", lambda firestore: VersionCatalog())
    session = _session().model_copy(
        update={"status": BrowserStatus.TAKEOVER, "takeover_subject": "user_one"}
    )
    action = BrowserAction(
        id="action_one",
        session_id=session.id,
        kind=BrowserActionKind.CLICK,
        selector=Selector(kind=SelectorKind.CSS, value="#revoke"),
        fencing_token=session.fencing_token,
    )

    with pytest.raises(CapabilityError, match="protected playbook control"):
        await workerapp._validate_takeover_action(
            cast(Any, SimpleNamespace(firestore=None, driver=ElementDriver())),
            session,
            action,
        )


@pytest.mark.anyio
async def test_computer_use_enables_injection_detection_and_parses_supported_action() -> None:
    google = ComputerGoogle("click")
    client = ComputerUseClient(google, "project-one")  # type: ignore[arg-type]

    proposal = await client.propose("click the approved control", b"image")

    assert proposal is not None
    assert proposal.name == "click"
    assert proposal.requires_confirmation is True
    computer = google.body["tools"][0]["computerUse"]
    assert computer["enablePromptInjectionDetection"] is True
    assert "navigate" not in computer["excludedPredefinedFunctions"]


@pytest.mark.anyio
async def test_computer_use_rejects_model_navigation() -> None:
    client = ComputerUseClient(ComputerGoogle("navigate"), "project-one")  # type: ignore[arg-type]

    with pytest.raises(ConnectorError, match="unsupported browser action"):
        await client.propose("go to an approved URL", b"image")


@pytest.mark.anyio
async def test_computer_worker_masks_declared_secret_fields_before_model_proposal() -> None:
    class Driver:
        def __init__(self) -> None:
            self.masked: tuple[Selector, ...] = ()

        async def screenshot(
            self, session: BrowserSession, masked_selectors: tuple[Selector, ...]
        ) -> bytes:
            del session
            self.masked = masked_selectors
            return b"masked-frame"

        async def validate_step(self, step: PlaybookStep) -> None:
            del step

    class Model:
        async def propose(self, *args: object) -> None:
            return None

    selector = Selector(kind=SelectorKind.TEST_ID, value="new-api-key")
    driver = Driver()
    worker = ComputerUseWorker(
        cast(Any, Model()),
        cast(Any, driver),
        cast(Any, None),
        cast(Any, None),
        lambda prefix: prefix,
        (selector,),
    )
    session = _session().model_copy(update={"status": BrowserStatus.RUNNING})
    step = _computer_version().definition.steps[0]

    proposal = await worker.propose(session, step, "inspect the approved page")

    assert proposal is None
    assert driver.masked == (selector,)


def test_browser_domain_allowlist_does_not_accept_lookalikes() -> None:
    driver = BrowserDriver(None, _session().policy)  # type: ignore[arg-type]

    driver.validate_url("https://console.vendor.example.com/keys")
    with pytest.raises(AuthenticationRequiredError, match="allowlist"):
        driver.validate_url("https://console.vendor.example.com.attacker.test/keys")
    with pytest.raises(ResourceConflictError, match="credential-free"):
        driver.validate_url("https://user:password@console.vendor.example.com/keys")


@pytest.mark.anyio
async def test_auth_broker_accepts_only_allowlisted_playwright_state() -> None:
    broker = BrowserAuthBroker(AuthSecrets("console.vendor.example.com"))  # type: ignore[arg-type]
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        authorization_reference="projects/project/secrets/session/versions/1",
        capabilities=frozenset({"browser.authenticate"}),
        allowed_resources=("console.vendor.example.com",),
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )

    state = await broker.storage_state(connection, ("*.vendor.example.com",))

    assert state["cookies"][0]["name"] == "session"


@pytest.mark.anyio
async def test_auth_broker_rejects_cross_domain_cookie() -> None:
    broker = BrowserAuthBroker(AuthSecrets("attacker.example"))  # type: ignore[arg-type]
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        authorization_reference="projects/project/secrets/session/versions/1",
        capabilities=frozenset({"browser.authenticate"}),
        allowed_resources=("console.vendor.example.com",),
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ResourceConflictError, match="outside"):
        await broker.storage_state(connection, ("*.vendor.example.com",))


@pytest.mark.anyio
async def test_auth_broker_rejects_api_provider_connections() -> None:
    broker = BrowserAuthBroker(AuthSecrets("console.vendor.example.com"))  # type: ignore[arg-type]
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="sendgrid",
        display_name="SendGrid Admin",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="projects/project/secrets/admin/versions/1",
        capabilities=frozenset({"create", "revoke"}),
        allowed_resources=("sendgrid:*",),
        http=make_http_provider_api(),
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ResourceConflictError, match="browser connection"):
        await broker.storage_state(connection, ("*.vendor.example.com",))


def _session() -> BrowserSession:
    return BrowserSession(
        id="session_one",
        organisation_id="org_one",
        run_id="run_one",
        playbook_id="playbook_one",
        playbook_version="version_one",
        provider_connection_id="provider_one",
        status=BrowserStatus.PROVISIONING,
        policy=BrowserPolicy(
            allowed_domains=("*.vendor.example.com",),
            allowed_actions=frozenset(
                {
                    BrowserActionKind.NAVIGATE,
                    BrowserActionKind.CLICK,
                    BrowserActionKind.TYPE,
                }
            ),
        ),
        fencing_token=3,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )


class Catalog:
    def __init__(self, repository: Repository, run: RotationRun) -> None:
        self._repository = repository
        self._run = run

    async def get[T](self, path: str, model: type[T]) -> T:
        if model is BrowserSession:
            assert self._repository.session is not None
            return self._repository.session  # type: ignore[return-value]
        if model is RotationRun:
            return self._run  # type: ignore[return-value]
        raise AssertionError(f"unexpected catalog model for {path}")


class ComputerGoogle:
    def __init__(self, action: str) -> None:
        self.action = action
        self.body: dict[str, Any] = {}

    async def request(self, method: str, url: str, **kwargs: object) -> dict[str, Any]:
        assert method == "POST"
        assert url.endswith(":generateContent")
        body = kwargs.get("json")
        assert isinstance(body, dict)
        self.body = body
        return {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": self.action,
                                    "args": {
                                        "x": 500,
                                        "y": 500,
                                        "url": "https://vendor.example.com",
                                        "safety_decision": {
                                            "decision": "require_confirmation",
                                            "explanation": "confirm the browser action",
                                        },
                                    },
                                }
                            }
                        ],
                    }
                }
            ]
        }


class AuthSecret:
    def __init__(self, value: bytes) -> None:
        self.value = bytearray(value)

    def bytes(self) -> bytes:
        return bytes(self.value)

    def clear(self) -> None:
        for index in range(len(self.value)):
            self.value[index] = 0


class AuthSecrets:
    def __init__(self, domain: str) -> None:
        self.domain = domain

    async def access(self, version: str) -> AuthSecret:
        import json

        assert version.endswith("/versions/1")
        return AuthSecret(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "session",
                            "value": "opaque-cookie",
                            "domain": self.domain,
                            "path": "/",
                        }
                    ],
                    "origins": [],
                }
            ).encode()
        )


class SetupCatalog:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def create(self, path: str, value: Any) -> None:
        self.values[path] = value

    async def get[T](self, path: str, model: type[T]) -> T:
        from core.errors import ResourceNotFoundError

        if path not in self.values:
            raise ResourceNotFoundError(path)
        return cast(T, self.values[path])

    async def replace[T](
        self,
        path: str,
        model: type[T],
        expected_revision: int,
        update: Any,
    ) -> T:
        current = cast(Any, self.values[path])
        assert current.revision == expected_revision
        changed = update(current)
        assert changed.revision == expected_revision + 1
        self.values[path] = changed
        return cast(T, changed)


class SetupConnections:
    def __init__(self, connection: Connection, catalog: SetupCatalog) -> None:
        self.connection = connection
        self.catalog = catalog

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return self.connection

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        assert self.connection.revision == expected_revision
        self.connection = self.connection.model_copy(
            update={
                "authorization_reference": authorization_reference,
                "status": status,
                "updated_at": updated_at,
                "revision": expected_revision + 1,
            }
        )
        return self.connection

    async def complete_setup(
        self,
        current_session: SetupSession,
        changed_session: SetupSession,
        current_connection: Connection,
        changed_connection: Connection,
    ) -> tuple[SetupSession, Connection]:
        path = FirestorePaths.setup(current_session.organisation_id, current_session.id)
        assert self.catalog.values[path] == current_session
        assert self.connection == current_connection
        self.catalog.values[path] = changed_session
        self.connection = changed_connection
        return changed_session, changed_connection


class SetupVms:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str | None] = []

    async def create(
        self,
        organisation_id: str,
        session_id: str,
        expires_at: datetime,
        setup_token_hash: str | None = None,
        allowed_domains: tuple[str, ...] = (),
        storage_domains: tuple[str, ...] = (),
        secret_container: str | None = None,
    ) -> BrowserVm:
        self.created.append(
            {
                "session_id": session_id,
                "setup_token_hash": setup_token_hash,
                "domains": allowed_domains,
                "storage_domains": storage_domains,
                "secret_container": secret_container,
            }
        )
        return BrowserVm(
            instance=f"projects/p/zones/z/instances/fk-{session_id}",
            internal_address="10.0.0.2",
        )

    async def delete(self, instance: str) -> None:
        self.deleted.append(instance)


class SetupSecrets:
    def __init__(self, versions_error: Exception | None = None) -> None:
        self.versions_error = versions_error
        self.disabled: list[str] = []
        self.store_calls = 0

    async def versions(self, secret: str) -> tuple[dict[str, Any], ...]:
        if self.versions_error is not None:
            raise self.versions_error
        return ({"name": f"{secret}/versions/1", "state": "ENABLED"},)

    async def disable(self, version: str) -> dict[str, Any]:
        self.disabled.append(version)
        return {"name": version, "state": "DISABLED"}


def _browser_connection() -> Connection:
    return Connection(
        id="connection_browser",
        organisation_id="org_one",
        platform="internal-vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        capabilities=frozenset({"browser.execute"}),
        allowed_resources=("*.vendor.example.com",),
        status=ConnectionStatus.DISABLED,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _exported_state() -> dict[str, Any]:
    return {
        "cookies": [
            {
                "name": "session",
                "value": "vendor-cookie",
                "domain": "app.vendor.example.com",
                "path": "/",
            },
            {
                "name": "sso",
                "value": "idp-cookie",
                "domain": "accounts.google.com",
                "path": "/",
            },
        ],
        "origins": [],
    }


def _setup_service(
    catalog: SetupCatalog,
    connection: Connection,
    state: dict[str, Any] | None = None,
    versions_error: Exception | None = None,
    runs: Any = None,
) -> tuple[BrowserSetupService, SetupVms, SetupSecrets, SetupConnections]:
    secrets = SetupSecrets(versions_error)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/setup/store":
            secrets.store_calls += 1
            captured = state or {}
            cookies = captured.get("cookies", []) if isinstance(captured, dict) else []
            if not any(
                isinstance(cookie, dict)
                and str(cookie.get("domain", "")).endswith("vendor.example.com")
                for cookie in cookies
            ):
                return httpx.Response(409, json={"code": "no-provider-session"})
            return httpx.Response(
                200,
                json={
                    "secret_reference": ("projects/project-one/secrets/vendor-session/versions/2"),
                    "fingerprint": "a" * 64,
                },
            )
        raise AssertionError(f"unexpected {request.url.path}")

    vms = SetupVms()
    connections = SetupConnections(connection, catalog)
    service = BrowserSetupService(
        catalog,
        connections,
        vms,
        secrets,
        "https://gateway.firekey.example",
        lambda: NOW,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        runs=runs,
    )
    return service, vms, secrets, connections


@pytest.mark.anyio
async def test_setup_begin_boots_isolated_worker_with_setup_token() -> None:
    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection())

    session, token = await service.begin(
        "org_one",
        "connection_browser",
        "projects/project-one/secrets/vendor-session",
        "user_one",
        ("accounts.google.com",),
    )

    assert session.status is SetupStatus.READY
    assert session.revision == 1
    assert session.internal_address == "10.0.0.2"
    assert session.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(session.model_dump(mode="json"))
    created = vms.created[0]
    assert created["setup_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(created)
    assert created["domains"] == ("*.vendor.example.com", "accounts.google.com")
    assert created["storage_domains"] == ("*.vendor.example.com",)


@pytest.mark.anyio
async def test_setup_begin_rejects_api_connections() -> None:
    catalog = SetupCatalog()
    connection = _browser_connection().model_copy(
        update={
            "interface": ConnectionInterface.API,
            "authorization": ConnectionAuthorization.API_KEY,
        }
    )
    service, _, _, _ = _setup_service(catalog, connection)

    with pytest.raises(ResourceConflictError, match="browser connection"):
        await service.begin(
            "org_one",
            "connection_browser",
            "projects/project-one/secrets/vendor-session",
            "user_one",
        )


@pytest.mark.anyio
async def test_setup_complete_captures_only_the_provider_session() -> None:
    catalog = SetupCatalog()
    service, vms, secrets, _ = _setup_service(
        catalog, _browser_connection(), state=_exported_state()
    )
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    completed, connection, resumed = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )
    assert resumed == ()

    assert completed.status is SetupStatus.COMPLETE
    assert completed.auth_reference == "projects/project-one/secrets/vendor-session/versions/2"
    assert connection.status is ConnectionStatus.READY
    assert connection.authorization_reference == completed.auth_reference
    assert vms.deleted == [completed.worker_instance]
    assert secrets.store_calls == 1


@pytest.mark.anyio
async def test_setup_complete_rejects_a_wrong_token() -> None:
    catalog = SetupCatalog()
    service, _, _, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    session, _ = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    with pytest.raises(ResourceConflictError, match="token is invalid"):
        await service.complete("org_one", session.id, session.revision, "x" * 43, "user_one")


@pytest.mark.anyio
async def test_setup_complete_belongs_to_its_operator() -> None:
    catalog = SetupCatalog()
    service, _, _, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    with pytest.raises(ResourceConflictError, match="another operator"):
        await service.complete("org_one", session.id, session.revision, token, "user_two")


@pytest.mark.anyio
async def test_setup_complete_requires_a_captured_provider_session() -> None:
    foreign_only = {
        "cookies": [{"name": "sso", "value": "idp", "domain": "accounts.google.com", "path": "/"}],
        "origins": [],
    }
    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection(), state=foreign_only)
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    with pytest.raises(ResourceConflictError, match="no provider session"):
        await service.complete("org_one", session.id, session.revision, token, "user_one")
    stored = await catalog.get(FirestorePaths.setup("org_one", session.id), SetupSession)
    assert stored.status is SetupStatus.TERMINATED
    assert vms.deleted == [session.worker_instance]


@pytest.mark.anyio
async def test_setup_reconciles_an_ambiguous_worker_secret_write() -> None:
    class AmbiguousSecrets(SetupSecrets):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def versions(self, secret: str) -> tuple[dict[str, Any], ...]:
            self.calls += 1
            values = [{"name": f"{secret}/versions/1", "state": "ENABLED"}]
            if self.calls >= 3:
                values.append({"name": f"{secret}/versions/2", "state": "ENABLED"})
            return tuple(values)

    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    secrets = AmbiguousSecrets()
    service._secrets = cast(Any, secrets)
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("worker response was lost", request=request)

    service._http = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    with pytest.raises(ResourceConflictError, match="worker was unavailable"):
        await service.complete("org_one", session.id, session.revision, token, "user_one")

    assert secrets.disabled == ["projects/project-one/secrets/vendor-session/versions/2"]
    stored = await catalog.get(FirestorePaths.setup("org_one", session.id), SetupSession)
    assert stored.status is SetupStatus.TERMINATED
    assert vms.deleted == [session.worker_instance]


@pytest.mark.anyio
async def test_setup_terminates_when_reconciliation_baseline_cannot_be_read() -> None:
    class FailingBaseline(SetupSecrets):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def versions(self, secret: str) -> tuple[dict[str, Any], ...]:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("Secret Manager unavailable")
            return await super().versions(secret)

    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    service._secrets = cast(Any, FailingBaseline())
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    with pytest.raises(RuntimeError, match="Secret Manager unavailable"):
        await service.complete("org_one", session.id, session.revision, token, "user_one")

    stored = await catalog.get(FirestorePaths.setup("org_one", session.id), SetupSession)
    assert stored.status is SetupStatus.TERMINATED
    assert vms.deleted == [session.worker_instance]


@pytest.mark.anyio
async def test_setup_disables_stored_version_when_connection_update_fails() -> None:
    class BrokenConnections(SetupConnections):
        async def complete_setup(
            self, *args: Any, **kwargs: Any
        ) -> tuple[SetupSession, Connection]:
            raise RuntimeError("database write failed")

    catalog = SetupCatalog()
    service, vms, secrets, _ = _setup_service(
        catalog, _browser_connection(), state=_exported_state()
    )
    service._connections = cast(Any, BrokenConnections(_browser_connection(), catalog))
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        await service.complete("org_one", session.id, session.revision, token, "user_one")

    assert secrets.disabled == ["projects/project-one/secrets/vendor-session/versions/2"]
    stored = await catalog.get(FirestorePaths.setup("org_one", session.id), SetupSession)
    assert stored.status is SetupStatus.TERMINATED
    assert vms.deleted == [session.worker_instance]


@pytest.mark.anyio
async def test_setup_recovers_an_ambiguous_atomic_completion() -> None:
    class AmbiguousConnections(SetupConnections):
        async def complete_setup(
            self,
            current_session: SetupSession,
            changed_session: SetupSession,
            current_connection: Connection,
            changed_connection: Connection,
        ) -> tuple[SetupSession, Connection]:
            await super().complete_setup(
                current_session,
                changed_session,
                current_connection,
                changed_connection,
            )
            raise RuntimeError("setup completion response was lost")

    catalog = SetupCatalog()
    service, vms, secrets, connections = _setup_service(
        catalog, _browser_connection(), state=_exported_state()
    )
    ambiguous = AmbiguousConnections(_browser_connection(), catalog)
    service._connections = cast(Any, ambiguous)
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    completed, connection, _ = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )

    assert connections.connection.status is ConnectionStatus.DISABLED
    assert ambiguous.connection == connection
    assert ambiguous.connection.status is ConnectionStatus.READY
    assert secrets.disabled == []
    stored = await catalog.get(FirestorePaths.setup("org_one", session.id), SetupSession)
    assert stored == completed
    assert stored.status is SetupStatus.COMPLETE
    assert vms.deleted == [session.worker_instance]


@pytest.mark.anyio
async def test_setup_vm_metadata_contains_only_the_token_hash() -> None:
    class Compute:
        def __init__(self) -> None:
            self.body: dict[str, Any] = {}

        async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            if method == "POST":
                self.body = kwargs["json"]
                return {"name": "operation-one"}
            return {
                "networkInterfaces": [{"networkIP": "10.0.0.2"}],
                "disks": [{"autoDelete": True}],
                "shieldedInstanceConfig": {"enableSecureBoot": True},
            }

        async def wait_operation(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

    compute = Compute()
    manager = BrowserVmManager(
        cast(Any, compute),
        "project-one",
        "us-east1-b",
        "projects/project-one/instanceTemplates/browser",
        "p" * 43,
        "evidence-bucket",
        "us-east1",
        "us-east1-docker.pkg.dev/project-one/firekey/browser@sha256:" + "a" * 64,
    )
    raw = "setup-token-that-must-not-enter-metadata"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    await manager.create(
        "org_one",
        "setup_one",
        NOW + timedelta(minutes=30),
        setup_token_hash=token_hash,
        allowed_domains=("*.vendor.example.com",),
        storage_domains=("*.vendor.example.com",),
        secret_container="projects/project-one/secrets/vendor-session",
    )

    encoded = json.dumps(compute.body)
    assert raw not in encoded
    assert token_hash in encoded
    assert 'firekey-setup-token"' not in encoded


@pytest.mark.anyio
async def test_setup_abort_terminates_the_worker() -> None:
    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection())
    session, _ = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    aborted = await service.abort("org_one", session.id, session.revision, "user_one")

    assert aborted.status is SetupStatus.TERMINATED
    assert aborted.terminated_at is not None
    assert vms.deleted == [aborted.worker_instance]


@pytest.mark.anyio
async def test_setup_store_requires_the_setup_token_and_returns_metadata_only() -> None:
    from browser.workerapp import SetupRuntime, app

    class ExportContext:
        def __init__(self) -> None:
            self.pages: list[Any] = []

        async def storage_state(self) -> dict[str, Any]:
            return {
                "cookies": [
                    {
                        "name": "session",
                        "value": "cookie-value",
                        "domain": "app.vendor.example.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }

        async def clear_cookies(self) -> None:
            pass

    class Google:
        async def request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"name": "projects/project-one/secrets/vendor-session/versions/2"}

    token = "t" * 43
    app.state.setup = SetupRuntime(
        cast(Any, ExportContext()),
        cast(Any, None),
        hashlib.sha256(token.encode()).hexdigest(),
        ("*.vendor.example.com",),
        "projects/project-one/secrets/vendor-session",
        cast(Any, Google()),
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post("/v1/setup/store")
            wrong = await client.post("/v1/setup/store", headers={"X-FireKey-Setup": "x" * 43})
            stored = await client.post("/v1/setup/store", headers={"X-FireKey-Setup": token})
    finally:
        del app.state.setup

    assert missing.status_code == 422
    assert wrong.status_code == 403
    assert stored.status_code == 200
    assert stored.json()["secret_reference"].endswith("/versions/2")
    assert "storage_state" not in stored.json()
    assert "cookie-value" not in stored.text


class _LoginPage:
    def __init__(self, url: str) -> None:
        self.url = url


def _login_driver(url: str) -> BrowserDriver:
    policy = BrowserPolicy(
        allowed_domains=("*.vendor.example.com",),
        allowed_actions=frozenset({BrowserActionKind.NAVIGATE}),
        login_url_pattern="https://*.vendor.example.com/login*",
    )
    return BrowserDriver(cast(Any, _LoginPage(url)), policy)


@pytest.mark.anyio
async def test_driver_detects_the_provider_login_wall() -> None:
    driver = _login_driver("https://app.vendor.example.com/login")
    step = PlaybookStep(
        id="step_one",
        stage=Stage.CREATE,
        tool="browser.click",
        operation="create-key",
        objective="create the replacement key",
        selectors=(Selector(kind=SelectorKind.TEST_ID, value="create"),),
        checkpoint=PageCheckpoint(url_pattern="https://app.vendor.example.com/keys"),
        evidence_checks=frozenset({"created"}),
    )

    with pytest.raises(AuthenticationRequiredError, match="login page"):
        await driver.validate_step(step)


@pytest.mark.anyio
async def test_driver_detects_a_login_redirect_after_navigation() -> None:
    class NavigatingPage:
        def __init__(self) -> None:
            self.url = "https://app.vendor.example.com/keys"

        async def goto(self, url: str, wait_until: str = "") -> None:
            self.url = url

    driver = BrowserDriver(cast(Any, NavigatingPage()), _login_driver("")._policy)
    action = BrowserAction(
        id="action_one",
        session_id="session_one",
        kind=BrowserActionKind.NAVIGATE,
        url="https://app.vendor.example.com/login",
        fencing_token=1,
    )

    with pytest.raises(AuthenticationRequiredError, match="login page"):
        await driver.execute(action)


@pytest.mark.anyio
async def test_coordinator_maps_the_login_wall_to_a_reauthentication_pause() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"code": "authentication-required", "message": "login page"}
        )

    executor = BrowserStepExecutor(
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        CapabilitySigner(b"\x01" * 32),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    run = make_run(NOW).model_copy(update={"fencing_token": 3})
    session = _session().model_copy(update={"internal_address": "10.0.0.2"})

    with pytest.raises(BrowserPauseError) as captured:
        await executor._post(run, session, "browser.operate", "/v1/steps/propose", {})

    assert captured.value.output["authentication_required"] is True
    assert captured.value.output["connection_id"] == "provider_one"


class FlagCatalog:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.replaced = False
        self.waiters: dict[str, ConnectionWaiter] = {}

    async def get[T](self, path: str, model: type[T]) -> T:
        if model is ConnectionWaiter:
            if path not in self.waiters:
                raise ResourceNotFoundError(path)
            return cast(T, self.waiters[path])
        return cast(T, self.connection)

    async def create(self, path: str, value: ConnectionWaiter) -> None:
        self.waiters[path] = value

    async def replace[T](
        self,
        path: str,
        model: type[T],
        expected_revision: int,
        update: Any,
    ) -> T:
        if model is ConnectionWaiter:
            current = self.waiters[path]
            changed = update(current)
            self.waiters[path] = changed
            return cast(T, changed)
        self.replaced = True
        self.connection = update(self.connection)
        return cast(T, self.connection)


class FlagNotifications:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def emit(
        self,
        event_id: str,
        organisation_id: str,
        kind: Any,
        severity: Any,
        title: str,
        body: str,
        link_path: str,
        resource_id: str,
        **kwargs: Any,
    ) -> tuple[Any, bool]:
        self.sent.append({"kind": kind, "resource_id": resource_id, "run_id": kwargs.get("run_id")})
        return None, True


@pytest.mark.anyio
async def test_reauthentication_flags_the_connection_and_notifies() -> None:
    connection = _browser_connection().model_copy(
        update={
            "status": ConnectionStatus.READY,
            "authorization_reference": "projects/p/secrets/s/versions/1",
        }
    )
    catalog = FlagCatalog(connection)
    notifications = FlagNotifications()

    await _flag_reauthentication(
        cast(Any, catalog),
        cast(Any, notifications),
        NOW,
        make_run(NOW),
        {"authentication_required": True, "connection_id": "connection_browser"},
        "execution_one",
    )

    assert catalog.connection.status is ConnectionStatus.REAUTHENTICATION
    assert notifications.sent[0]["kind"] is NotificationKind.CONNECTION_UNHEALTHY
    waiter = next(iter(catalog.waiters.values()))
    assert waiter.run_ids == ("run_one",)
    assert notifications.sent[0]["run_id"] == "run_one"


@pytest.mark.anyio
async def test_reauthentication_ignores_output_without_a_connection() -> None:
    catalog = FlagCatalog(_browser_connection())
    notifications = FlagNotifications()

    await _flag_reauthentication(
        cast(Any, catalog),
        cast(Any, notifications),
        NOW,
        make_run(NOW),
        {"authentication_required": True},
        "execution_one",
    )

    assert not catalog.replaced
    assert notifications.sent == []


@pytest.mark.anyio
async def test_reauthentication_remembers_the_paused_run_when_connection_flagging_fails() -> None:
    class FailingConnectionCatalog(FlagCatalog):
        async def replace[T](
            self,
            path: str,
            model: type[T],
            expected_revision: int,
            update: Any,
        ) -> T:
            if model is Connection:
                raise RuntimeError("catalog unavailable")
            return await super().replace(path, model, expected_revision, update)

    catalog = FailingConnectionCatalog(_browser_connection())
    notifications = FlagNotifications()

    await _flag_reauthentication(
        cast(Any, catalog),
        cast(Any, notifications),
        NOW,
        make_run(NOW),
        {"authentication_required": True, "connection_id": "connection_browser"},
        "execution_one",
    )

    waiter = next(iter(catalog.waiters.values()))
    assert waiter.run_ids == ("run_one",)
    assert notifications.sent[0]["run_id"] == "run_one"


class GatewayRepository:
    def __init__(self, session: SetupSession) -> None:
        self._session = session

    async def setup(self, organisation_id: str, setup_id: str) -> SetupSession:
        return self._session


class GatewayAccess:
    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        return PrincipalGrant(subject=identity.subject, roles=frozenset({Role.ADMINISTRATOR}))


def _setup_session(
    token: str,
    subject: str = "user_one",
    status: SetupStatus = SetupStatus.READY,
) -> SetupSession:
    return SetupSession(
        id="setup_one",
        organisation_id="org_one",
        connection_id="connection_browser",
        secret_container="projects/project-one/secrets/vendor-session",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        subject=subject,
        allowed_domains=("*.vendor.example.com",),
        worker_instance="projects/p/zones/z/instances/fk-setup_one",
        internal_address="10.0.0.2",
        status=status,
        created_at=NOW,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_setup_gateway_authorises_the_owning_operator() -> None:
    token = "t" * 43
    gateway = BrowserSessionGateway(
        cast(Any, GatewayRepository(_setup_session(token))),
        AccessControl(GatewayAccess()),
        cast(Any, None),
        cast(Any, None),
    )
    identity = AuthenticatedIdentity(subject="user_one", issuer="test")

    organisation_id, session, returned = await gateway._authorise_setup(
        {"organisation_id": "org_one", "setup_id": "setup_one", "token": token},
        identity,
    )

    assert organisation_id == "org_one"
    assert session.id == "setup_one"
    assert returned == token


@pytest.mark.anyio
async def test_setup_gateway_rejects_foreign_operators_and_bad_tokens() -> None:
    token = "t" * 43
    gateway = BrowserSessionGateway(
        cast(Any, GatewayRepository(_setup_session(token))),
        AccessControl(GatewayAccess()),
        cast(Any, None),
        cast(Any, None),
    )

    with pytest.raises(CapabilityError, match="another operator"):
        await gateway._authorise_setup(
            {"organisation_id": "org_one", "setup_id": "setup_one", "token": token},
            AuthenticatedIdentity(subject="user_two", issuer="test"),
        )

    with pytest.raises(CapabilityError, match="token is invalid"):
        await gateway._authorise_setup(
            {"organisation_id": "org_one", "setup_id": "setup_one", "token": "x" * 43},
            AuthenticatedIdentity(subject="user_one", issuer="test"),
        )


@pytest.mark.anyio
async def test_setup_begin_rejects_an_unwritable_secret_container() -> None:
    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(
        catalog, _browser_connection(), versions_error=RuntimeError("missing")
    )

    with pytest.raises(ResourceConflictError, match="secret container is not writable"):
        await service.begin(
            "org_one",
            "connection_browser",
            "projects/project-one/secrets/vendor-session",
            "user_one",
        )
    assert vms.created == []


@pytest.mark.anyio
async def test_setup_begin_terminates_the_session_when_worker_readiness_fails() -> None:
    from core.storage.paths import FirestorePaths

    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection())

    async def not_ready(session: SetupSession) -> None:
        del session
        raise ResourceConflictError("setup worker did not become ready")

    service._wait_ready = not_ready  # type: ignore[method-assign]

    with pytest.raises(ResourceConflictError, match="did not become ready"):
        await service.begin(
            "org_one",
            "connection_browser",
            "projects/project-one/secrets/vendor-session",
            "user_one",
        )

    stored = await catalog.get(FirestorePaths.setup("org_one", "setup_browser"), SetupSession)
    assert stored.status is SetupStatus.TERMINATED
    assert vms.deleted == [stored.worker_instance]


@pytest.mark.anyio
async def test_setup_complete_resumes_paused_runs_waiting_on_the_connection() -> None:
    from core.storage.paths import FirestorePaths

    class Resumer:
        def __init__(self) -> None:
            self.ids: tuple[str, ...] = ()

        async def resume(
            self, organisation_id: str, run_ids: tuple[str, ...], actor_id: str
        ) -> tuple[str, ...]:
            del organisation_id, actor_id
            self.ids = run_ids
            return run_ids

    catalog = SetupCatalog()
    resumer = Resumer()
    service, _, _, _ = _setup_service(
        catalog, _browser_connection(), state=_exported_state(), runs=resumer
    )
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )
    waiter_path = FirestorePaths.connection_waiter("org_one", "connection_browser")
    await catalog.create(
        waiter_path,
        ConnectionWaiter(
            organisation_id="org_one",
            connection_id="connection_browser",
            run_ids=("run_one", "run_two"),
        ),
    )

    _, _, resumed = await service.complete(
        "org_one", session.id, session.revision, token, "user_one", "actor_one"
    )

    assert resumed == ("run_one", "run_two")
    assert resumer.ids == ("run_one", "run_two")
    waiter = await catalog.get(waiter_path, ConnectionWaiter)
    assert waiter.run_ids == ()


@pytest.mark.anyio
async def test_setup_completion_keeps_waiting_runs_that_did_not_resume() -> None:
    from core.storage.paths import FirestorePaths

    class PartialResumer:
        async def resume(
            self, organisation_id: str, run_ids: tuple[str, ...], actor_id: str
        ) -> tuple[str, ...]:
            del organisation_id, actor_id
            assert run_ids == ("run_one", "run_two")
            return ("run_one",)

    catalog = SetupCatalog()
    service, _, _, _ = _setup_service(
        catalog, _browser_connection(), state=_exported_state(), runs=PartialResumer()
    )
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )
    waiter_path = FirestorePaths.connection_waiter("org_one", "connection_browser")
    await catalog.create(
        waiter_path,
        ConnectionWaiter(
            organisation_id="org_one",
            connection_id="connection_browser",
            run_ids=("run_one", "run_two"),
        ),
    )

    _, _, resumed = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )

    assert resumed == ("run_one",)
    waiter = await catalog.get(waiter_path, ConnectionWaiter)
    assert waiter.run_ids == ("run_two",)


@pytest.mark.anyio
async def test_setup_completion_retry_resumes_runs_after_transient_resume_failure() -> None:
    class FlakyResumer:
        def __init__(self) -> None:
            self.calls = 0

        async def resume(
            self, organisation_id: str, run_ids: tuple[str, ...], actor_id: str
        ) -> tuple[str, ...]:
            del organisation_id, actor_id
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("workflow unavailable")
            return run_ids

    catalog = SetupCatalog()
    resumer = FlakyResumer()
    service, _, secrets, _ = _setup_service(
        catalog, _browser_connection(), state=_exported_state(), runs=resumer
    )
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )
    waiter_path = FirestorePaths.connection_waiter("org_one", "connection_browser")
    await catalog.create(
        waiter_path,
        ConnectionWaiter(
            organisation_id="org_one",
            connection_id="connection_browser",
            run_ids=("run_one",),
        ),
    )

    with pytest.raises(RuntimeError, match="workflow unavailable"):
        await service.complete("org_one", session.id, session.revision, token, "user_one")
    stored = await catalog.get(FirestorePaths.setup("org_one", session.id), SetupSession)
    assert stored.status is SetupStatus.COMPLETE

    _, _, resumed = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )

    assert resumed == ("run_one",)
    assert resumer.calls == 2
    assert secrets.store_calls == 1
    waiter = await catalog.get(waiter_path, ConnectionWaiter)
    assert waiter.run_ids == ()


@pytest.mark.anyio
async def test_setup_completion_claims_the_session_before_writing_a_secret() -> None:

    catalog = SetupCatalog()
    service, _, _, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    completed, _, _ = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )

    assert completed.status is SetupStatus.COMPLETE


@pytest.mark.anyio
async def test_setup_completion_replays_without_creating_another_secret_version() -> None:
    catalog = SetupCatalog()
    service, _, secrets, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )
    completed, connection, _ = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )

    replayed, replayed_connection, resumed = await service.complete(
        "org_one", session.id, session.revision, token, "user_one"
    )

    assert replayed == completed
    assert replayed_connection == connection
    assert resumed == ()
    assert secrets.store_calls == 1


def test_blocked_redirect_is_authentication_required() -> None:
    driver = _login_driver("https://app.vendor.example.com/keys")
    driver._blocked_egress = True

    with pytest.raises(AuthenticationRequiredError, match="off the allowlist"):
        driver._check_blocked_egress()


def test_ready_browser_connections_are_required_before_resume() -> None:
    from core.playbook import require_ready_browser_connections

    waiting = _browser_connection()
    with pytest.raises(ResourceConflictError, match="still needs login"):
        require_ready_browser_connections((waiting,))
    ready = waiting.model_copy(
        update={
            "status": ConnectionStatus.READY,
            "authorization_reference": "projects/p/secrets/s/versions/1",
        }
    )
    require_ready_browser_connections((ready,))
    expired = ready.model_copy(update={"authorization_expires_at": NOW})
    with pytest.raises(ResourceConflictError, match="still needs login"):
        require_ready_browser_connections((expired,))


@pytest.mark.anyio
async def test_setup_begin_rejects_a_second_active_session() -> None:
    catalog = SetupCatalog()
    service, _, _, _ = _setup_service(catalog, _browser_connection())
    await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )

    with pytest.raises(ResourceConflictError, match="already active"):
        await service.begin(
            "org_one",
            "connection_browser",
            "projects/project-one/secrets/vendor-session",
            "user_one",
        )


@pytest.mark.anyio
async def test_setup_begin_replaces_a_finished_session() -> None:
    catalog = SetupCatalog()
    service, vms, _, _ = _setup_service(catalog, _browser_connection(), state=_exported_state())
    session, token = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_one"
    )
    await service.complete("org_one", session.id, session.revision, token, "user_one")

    restarted, _ = await service.begin(
        "org_one", "connection_browser", "projects/project-one/secrets/vendor-session", "user_two"
    )

    assert restarted.id == session.id
    assert restarted.status is SetupStatus.READY
    assert restarted.subject == "user_two"
    assert restarted.revision > session.revision
    assert len(vms.created) == 2


@pytest.mark.anyio
async def test_terminated_browser_can_be_reprovisioned() -> None:
    repository = Repository()
    service = BrowserService(repository, lambda: NOW)
    session = await service.create(_session())
    session = await service.attach(
        "org_one", session.id, session.revision, "instances/worker", "10.2.0.4"
    )
    session = await service.terminate("org_one", session.id, session.revision)

    session = await service.reprovision(
        "org_one",
        session.id,
        session.revision,
        "connection_browser",
        session.policy,
        4,
        NOW + timedelta(hours=2),
    )

    assert session.status is BrowserStatus.PROVISIONING
    assert session.provider_connection_id == "connection_browser"
    assert session.worker_instance is None
    assert session.fencing_token == 4
    assert session.terminated_at is None


@pytest.mark.anyio
async def test_executor_binds_the_browser_connection() -> None:
    connection = _browser_connection().model_copy(
        update={
            "status": ConnectionStatus.READY,
            "authorization_reference": "projects/p/secrets/s/versions/1",
        }
    )
    version = _computer_version()
    run = make_run(NOW).model_copy(update={"fencing_token": 3})
    assignment = PlaybookAssignment(
        id="assignment_one",
        organisation_id="org_one",
        credential_id="cred_one",
        playbook_id="playbook_one",
        version_id="version_one",
        connection_ids=("connection_browser",),
        assigned_by="admin_one",
        assigned_at=NOW,
    )
    catalog = SessionCatalog(version, {"connection_browser": connection})
    vms = SetupVms()
    sessions = BrowserService(Repository(), lambda: NOW)
    executor = BrowserStepExecutor(
        cast(Any, catalog),
        sessions,
        cast(Any, vms),
        CapabilitySigner(b"\x01" * 32),
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"status": "ok"})
            )
        ),
    )

    session = await executor._session(run, assignment)

    assert session.provider_connection_id == "connection_browser"
    assert session.policy.login_url_pattern == "https://*.vendor.example.com/login*"
    assert vms.created[0]["session_id"] == session.id


def test_computer_use_preflight_requires_a_browser_connection() -> None:
    from coordinator.service import required_connection_roles

    expected = frozenset(
        {ConnectionRole.PROVIDER, ConnectionRole.SECRET_STORE, ConnectionRole.RUNTIME}
    )
    assert required_connection_roles(ExecutionMethod.COMPUTER) == expected
    assert required_connection_roles(ExecutionMethod.API) == expected


class SessionCatalog:
    def __init__(self, version: PlaybookVersion, connections: dict[str, Connection]) -> None:
        self.version = version
        self.connections = connections
        self.session: BrowserSession | None = None

    async def get[T](self, path: str, model: type[T]) -> T:
        from core.errors import ResourceNotFoundError

        if model is BrowserSession:
            if self.session is None:
                raise ResourceNotFoundError(path)
            return self.session  # type: ignore[return-value]
        if model is PlaybookVersion:
            return self.version  # type: ignore[return-value]
        if model is Connection:
            for connection_id, connection in self.connections.items():
                if path.endswith(f"/{connection_id}"):
                    return connection  # type: ignore[return-value]
            raise ResourceNotFoundError(path)
        raise AssertionError(f"unexpected catalog model for {path}")


def _computer_version() -> PlaybookVersion:
    from policy import digest

    stages = (
        Stage.CREATE,
        Stage.STORE,
        Stage.DEPLOY,
        Stage.VERIFY,
        Stage.ROLLOUT,
        Stage.OBSERVE,
        Stage.REVOKE,
    )
    steps = tuple(
        PlaybookStep(
            id=f"step_{stage.value}",
            stage=stage,
            tool="browser.secure-capture" if stage is Stage.CREATE else f"test.{stage.value}",
            operation=stage.value,
            objective=f"Execute the {stage.value} lifecycle stage",
            selectors=(
                (Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),)
                if stage is Stage.CREATE
                else ()
            ),
            checkpoint=PageCheckpoint(url_pattern="https://app.vendor.example.com/keys")
            if stage is Stage.CREATE
            else None,
            protected=stage in {Stage.CREATE, Stage.REVOKE},
            secure_field=SecureField(
                name="api_key",
                selector=Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),
                sink_connection_id="sink_one",
                secret_resource="projects/project-one/secrets/key",
                provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="new-key-id"),
            )
            if stage is Stage.CREATE
            else None,
            evidence_checks=frozenset({f"{stage.value}-passed"}),
        )
        for stage in stages
    )
    definition = PlaybookDraft(
        name="Vendor rotation",
        provider="vendor",
        execution=ExecutionMethod.COMPUTER,
        allowed_domains=("*.vendor.example.com",),
        allowed_tools=frozenset(step.tool for step in steps),
        required_connections=("connection_browser", "secret_one", "runtime_one"),
        steps=steps,
        recovery={
            stage.value: RecoveryBranch(
                mode="rollforward" if stage is Stage.REVOKE else "rollback",
                actions=(
                    RecoveryAction(
                        tool="test.recover",
                        operation="recover",
                        parameters={"connection_id": "runtime_one"},
                    ),
                ),
                preserves_old_generation=stage is not Stage.REVOKE,
            )
            for stage in stages
        },
        login_url_pattern="https://*.vendor.example.com/login*",
    )
    return PlaybookVersion(
        id="version_one",
        organisation_id="org_one",
        playbook_id="playbook_one",
        number=1,
        definition=definition,
        digest=digest(definition),
        state=PlaybookState.ACTIVE,
        dry_run_id="dryrun_one",
        approved_by="admin_one",
        approved_at=NOW,
        created_by="author_one",
        created_at=NOW,
    )
