from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from contracts import (
    AgentResult,
    AgentTask,
    ControlDefinition,
    ControlVersion,
    CredentialGeneration,
    GenerationState,
    RunStatus,
    Stage,
    StageExecutionRequest,
    StageExecutionStatus,
    VerificationReport,
    VerificationStatus,
)
from coordinator.browser import is_deterministic_browser_step
from coordinator.service import (
    StageCoordinator,
    _operator_objective,
    _operator_task_id,
    _requires_provider_revocation,
)
from core.storage.paths import FirestorePaths
from testkit import make_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def test_browser_bootstrap_generation_skips_provider_revocation() -> None:
    assert _requires_provider_revocation(True, None) is False
    assert _requires_provider_revocation(True, "resend-key-one") is True
    assert _requires_provider_revocation(False, None) is True


def test_operator_objective_is_policy_safe_and_step_bound() -> None:
    objective = _operator_objective("create_navigate_api_keys")

    assert objective == (
        "Review immutable browser step create_navigate_api_keys "
        "for isolated Computer Use readiness."
    )
    assert "Do not" not in objective


def test_operator_task_id_is_stable_within_a_lease_and_changes_after_resume() -> None:
    first = _operator_task_id("run_one", "create_navigate", 4)

    assert first == _operator_task_id("run_one", "create_navigate", 4)
    assert first != _operator_task_id("run_one", "create_navigate", 5)


def test_declared_browser_controls_bypass_operator_agent() -> None:
    from contracts import PageCheckpoint, PlaybookStep, Selector, SelectorKind

    navigate = PlaybookStep(
        id="navigate",
        stage=Stage.CREATE,
        tool="browser.navigate",
        operation="navigate",
        objective="Open the provider page",
        parameters={"url": "https://resend.com/api-keys"},
        checkpoint=PageCheckpoint(url_pattern="https://resend.com/api-keys"),
        evidence_checks=frozenset({"opened"}),
    )
    unsupported = PlaybookStep(
        id="operate",
        stage=Stage.CREATE,
        tool="browser.operate",
        operation="operate",
        objective="Complete an unsupported operation",
        checkpoint=PageCheckpoint(url_pattern="https://resend.com/api-keys"),
        selectors=(Selector(kind=SelectorKind.TEST_ID, value="unsupported"),),
        evidence_checks=frozenset({"operated"}),
    )

    assert is_deterministic_browser_step(navigate)
    assert not is_deterministic_browser_step(unsupported)


class MemoryCatalog:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.client = MemoryClient(self.store)

    async def get(self, path: str, model: Any) -> Any:
        if path not in self.store:
            from core.errors import ResourceNotFoundError

            raise ResourceNotFoundError(f"document {path} not found")
        data = self.store[path]
        if hasattr(model, "model_validate"):
            return model.model_validate(data)
        return data

    async def create(self, path: str, item: Any) -> Any:
        self.store[path] = item.model_dump() if hasattr(item, "model_dump") else item
        return item

    async def update(self, path: str, expected_revision: int, item: Any) -> Any:
        self.store[path] = item.model_dump() if hasattr(item, "model_dump") else item
        return item

    async def set(self, path: str, item: Any) -> Any:
        self.store[path] = item.model_dump() if hasattr(item, "model_dump") else item
        return item


class MemoryClient:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def document(self, path: str) -> Any:
        return MemoryDoc(self._store, path)


class MemoryDoc:
    def __init__(self, store: dict[str, Any], path: str) -> None:
        self._store = store
        self._path = path

    async def get(self) -> Any:
        exists = self._path in self._store
        data = self._store.get(self._path)
        return MemorySnapshot(exists, data)


class MemorySnapshot:
    def __init__(self, exists: bool, data: Any) -> None:
        self.exists = exists
        self._data = data

    def to_dict(self) -> Any:
        return self._data


class Broker:
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        from contracts import ToolResult

        return ToolResult(
            request_id="req_1",
            succeeded=True,
            result={"status": "ok"},
            evidence_ids=("evidence_tool_1",),
        )


class Browser:
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return {"status": "ok"}

    async def terminate(self, *args: Any, **kwargs: Any) -> None:
        pass


class Agents:
    async def execute(self, task: AgentTask) -> AgentResult:
        if task.skill == "detect_stale_mapping":
            return AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=True,
                output={"missing_inventory": False},
                evidence_ids=("evidence_agent_1",),
                completed_at=NOW,
            )
        if task.skill == "plan_rotation":
            return AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=True,
                output={"decision": "plan", "strategy": "parallel"},
                evidence_ids=("evidence_plan_1",),
                completed_at=NOW,
            )
        return AgentResult(
            task_id=task.id,
            agent=task.agent,
            skill=task.skill,
            succeeded=True,
            output={"decision": "ok"},
            evidence_ids=("evidence_default",),
            completed_at=NOW,
        )


class Verifier:
    async def verify(self, *args: Any, **kwargs: Any) -> VerificationReport:
        return VerificationReport(
            id="report_1",
            organisation_id="org_one",
            run_id="run_one",
            generation_id="gen_2",
            status=VerificationStatus.PASSED,
            results=(),
            checks=frozenset({"http-status-matched"}),
            evidence_ids=("evidence_verify_1",),
            started_at=NOW,
            completed_at=NOW,
        )


class Generations:
    async def create(self, *args: Any, **kwargs: Any) -> CredentialGeneration:
        return CredentialGeneration(
            id="gen_2",
            organisation_id="org_one",
            credential_id="cred_one",
            provider_id="key_2",
            scopes=frozenset({"mail.send"}),
            state=GenerationState.ACTIVE,
            attempt_id="attempt_1",
            secret_reference="projects/p/secrets/s/versions/2",
            created_at=NOW,
        )


class Incidents:
    pass


class EvidenceSink:
    async def store(self, *args: Any, **kwargs: Any) -> Any:
        from contracts import Evidence

        return Evidence(
            id="evidence_1",
            organisation_id="org_one",
            kind="test",
            resource="gs://bucket/test",
            digest="a" * 64,
            content_type="application/json",
            size=10,
            created_at=NOW,
            region="us-central1",
        )


class AuditWriter:
    async def append(self, *args: Any, **kwargs: Any) -> Any:
        pass


@pytest.mark.anyio
async def test_coordinator_executes_trigger_stage() -> None:
    catalog = MemoryCatalog()
    coordinator = StageCoordinator(
        catalog=catalog,  # type: ignore[arg-type]
        broker=Broker(),  # type: ignore[arg-type]
        browser=Browser(),  # type: ignore[arg-type]
        agents=Agents(),  # type: ignore[arg-type]
        verifier=Verifier(),  # type: ignore[arg-type]
        generations=Generations(),  # type: ignore[arg-type]
        incidents=Incidents(),  # type: ignore[arg-type]
        evidence=EvidenceSink(),  # type: ignore[arg-type]
        audit=AuditWriter(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    from contracts import Lease

    run = make_run(NOW).model_copy(
        update={
            "id": "run_one",
            "organisation_id": "org_one",
            "stage": Stage.TRIGGER,
            "status": RunStatus.RUNNING,
            "revision": 1,
            "fencing_token": 1,
            "control_version": "pol_ver_1",
            "lease": Lease(
                owner_id="worker_1", fencing_token=1, expires_at=NOW + timedelta(hours=1)
            ),
        }
    )
    from contracts import RecoveryMode
    from policy import digest
    from policy.rules import REQUIRED_CHECKS

    controls_definition = ControlDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"provider.createCredential", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        require_revoke_approval=True,
    )
    controls_version = ControlVersion(
        id="pol_ver_1",
        organisation_id="org_one",
        credential_id=run.credential_id,
        number=1,
        definition=controls_definition,
        digest=digest(controls_definition),
        created_by="admin_one",
        created_at=NOW,
    )
    catalog.store[FirestorePaths.control_version("org_one", run.credential_id, "pol_ver_1")] = (
        controls_version.model_dump()
    )
    catalog.store[FirestorePaths.run("org_one", "run_one")] = run.model_dump()
    catalog.store[FirestorePaths.dedupe("org_one", run.trigger.source, run.trigger.event_id)] = {
        "run_id": "run_one"
    }

    request = StageExecutionRequest(
        organisation_id="org_one",
        run_id="run_one",
        stage=Stage.TRIGGER,
        expected_revision=1,
        fencing_token=1,
    )

    result = await coordinator.execute(request)

    assert result.status is StageExecutionStatus.SUCCEEDED
    assert "request-authenticated" in result.checks
    assert "source-deduplicated" in result.checks


@pytest.mark.anyio
async def test_coordinator_skips_optional_human_approval() -> None:
    catalog = MemoryCatalog()
    coordinator = StageCoordinator(
        catalog=catalog,  # type: ignore[arg-type]
        broker=Broker(),  # type: ignore[arg-type]
        browser=Browser(),  # type: ignore[arg-type]
        agents=Agents(),  # type: ignore[arg-type]
        verifier=Verifier(),  # type: ignore[arg-type]
        generations=Generations(),  # type: ignore[arg-type]
        incidents=Incidents(),  # type: ignore[arg-type]
        evidence=EvidenceSink(),  # type: ignore[arg-type]
        audit=AuditWriter(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    from contracts import Lease, RecoveryMode
    from policy import digest
    from policy.rules import REQUIRED_CHECKS

    checks = dict(REQUIRED_CHECKS)
    checks[Stage.PREFLIGHT] = checks[Stage.PREFLIGHT].difference({"approvers-known"})
    checks[Stage.APPROVAL] = frozenset({"approval-not-required", "evidence-current"})
    definition = ControlDefinition(
        required_checks=checks,
        allowed_tools=frozenset({"provider.revokeCredential", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        require_revoke_approval=False,
    )
    version = ControlVersion(
        id="control_automatic",
        organisation_id="org_one",
        credential_id="cred_one",
        number=1,
        definition=definition,
        digest=digest(definition),
        created_by="admin_one",
        created_at=NOW,
    )
    run = make_run(NOW).model_copy(
        update={
            "id": "run_automatic",
            "organisation_id": "org_one",
            "credential_id": "cred_one",
            "stage": Stage.APPROVAL,
            "status": RunStatus.RUNNING,
            "revision": 9,
            "fencing_token": 2,
            "control_version": version.id,
            "lease": Lease(
                owner_id="worker_one",
                fencing_token=2,
                expires_at=NOW + timedelta(hours=1),
            ),
        }
    )
    catalog.store[FirestorePaths.control_version("org_one", "cred_one", version.id)] = (
        version.model_dump()
    )
    catalog.store[FirestorePaths.run("org_one", run.id)] = run.model_dump()

    result = await coordinator.execute(
        StageExecutionRequest(
            organisation_id="org_one",
            run_id=run.id,
            stage=Stage.APPROVAL,
            expected_revision=run.revision,
            fencing_token=run.fencing_token,
        )
    )

    assert result.status is StageExecutionStatus.SUCCEEDED
    assert result.checks == frozenset({"approval-not-required", "evidence-current"})
    assert result.output == {"approval_required": False}
