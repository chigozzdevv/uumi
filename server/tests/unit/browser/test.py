from datetime import UTC, datetime, timedelta

import pytest
from browser.driver import BrowserDriver
from browser.service import BrowserService
from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserActionRecord,
    BrowserActionStatus,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    ReplayCheckpoint,
    SecureCaptureResult,
    Selector,
    SelectorKind,
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


def test_browser_domain_allowlist_does_not_accept_lookalikes() -> None:
    driver = BrowserDriver(None, _session().policy)  # type: ignore[arg-type]

    driver.validate_url("https://console.vendor.example.com/keys")
    with pytest.raises(ResourceConflictError, match="outside"):
        driver.validate_url("https://console.vendor.example.com.attacker.test/keys")
    with pytest.raises(ResourceConflictError, match="credential-free"):
        driver.validate_url("https://user:password@console.vendor.example.com/keys")


def _session() -> BrowserSession:
    return BrowserSession(
        id="session_one",
        organisation_id="org_one",
        run_id="run_one",
        playbook_id="playbook_one",
        playbook_version="version_one",
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
