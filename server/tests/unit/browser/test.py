from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from broker import CapabilitySigner
from browser.access import BrowserAccessService
from browser.auth import BrowserAuthBroker
from browser.driver import BrowserDriver
from browser.model import ComputerUseClient
from browser.service import BrowserService
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
    ConnectionKind,
    ConnectionStatus,
    ReplayCheckpoint,
    RotationRun,
    RunStatus,
    SecureCaptureResult,
    Selector,
    SelectorKind,
    Trigger,
)
from core.errors import ResourceConflictError

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


def test_browser_domain_allowlist_does_not_accept_lookalikes() -> None:
    driver = BrowserDriver(None, _session().policy)  # type: ignore[arg-type]

    driver.validate_url("https://console.vendor.example.com/keys")
    with pytest.raises(ResourceConflictError, match="outside"):
        driver.validate_url("https://console.vendor.example.com.attacker.test/keys")
    with pytest.raises(ResourceConflictError, match="credential-free"):
        driver.validate_url("https://user:password@console.vendor.example.com/keys")


@pytest.mark.anyio
async def test_auth_broker_accepts_only_allowlisted_playwright_state() -> None:
    broker = BrowserAuthBroker(AuthSecrets("console.vendor.example.com"))  # type: ignore[arg-type]
    connection = Connection(
        id="provider_one",
        organisation_id="org_one",
        kind=ConnectionKind.PROVIDER,
        provider="vendor",
        display_name="Vendor console",
        auth_reference="projects/project/secrets/session/versions/1",
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
        kind=ConnectionKind.PROVIDER,
        provider="vendor",
        display_name="Vendor console",
        auth_reference="projects/project/secrets/session/versions/1",
        capabilities=frozenset({"browser.authenticate"}),
        allowed_resources=("console.vendor.example.com",),
        status=ConnectionStatus.READY,
        region="us-central1",
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ResourceConflictError, match="outside"):
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

    async def access(self, reference: str) -> AuthSecret:
        import json

        assert reference.endswith("/versions/1")
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
