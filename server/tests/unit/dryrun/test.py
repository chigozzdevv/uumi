from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    CreateRunCommand,
    DryRun,
    DryRunStatus,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookVersion,
    RotationRun,
    RunStatus,
    RunStep,
    Stage,
    StageProof,
    Trigger,
)
from core.playbook import PlaybookService
from core.storage.firestore import _advance_dryrun
from core.storage.repository import MutationResult
from core.workflow.trigger import build_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Repository:
    def __init__(self) -> None:
        self.existing: DryRun | None = None
        self.validated: tuple[str, ...] | None = None

    async def get_dryrun(
        self, organisation_id: str, playbook_id: str, dryrun_id: str
    ) -> DryRun | None:
        return self.existing

    async def validate_dryrun(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        environment_id: str,
        credential_id: str,
    ) -> None:
        self.validated = (
            organisation_id,
            playbook_id,
            version_id,
            environment_id,
            credential_id,
        )

    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: PlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[Playbook, PlaybookVersion]:
        raise AssertionError("not used")

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        raise AssertionError("not used")

    async def activate(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        dryrun_id: str,
        actor_id: str,
        activated_at: datetime,
    ) -> PlaybookVersion:
        raise AssertionError("not used")

    async def assign(self, assignment: PlaybookAssignment) -> PlaybookAssignment:
        raise AssertionError("not used")


class Workflow:
    def __init__(self) -> None:
        self.command: CreateRunCommand | None = None
        self.run: RotationRun | None = None

    async def create(self, command: CreateRunCommand) -> MutationResult:
        self.command = command
        self.run = build_run(command, command.run_id or "run_generated", NOW)
        step = RunStep(
            id=command.id,
            organisation_id=command.organisation_id,
            run_id=self.run.id,
            operation="create",
            command_hash="a" * 64,
            actor_id=command.trigger.actor_id,
            after_stage=Stage.TRIGGER,
            after_status=RunStatus.PENDING,
            revision=0,
            recorded_at=NOW,
        )
        return MutationResult(run=self.run, step=step, applied=True)

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        assert self.run is not None and self.run.id == run_id
        return self.run


@pytest.mark.anyio
async def test_dryrun_launches_real_isolated_rotation() -> None:
    repository = Repository()
    workflow = Workflow()
    service = PlaybookService(repository, lambda: NOW, workflow)

    dryrun, run, applied = await service.start_dryrun(
        "org_one",
        "playbook_one",
        "dryrun_one",
        "version_one",
        "environment_test",
        "credential_test",
        "policy_one",
        "actor_one",
        "command_one",
        "Exercise every lifecycle stage against the sandbox credential.",
        "planned",
        NOW,
    )

    assert applied is True
    assert dryrun.status is DryRunStatus.PENDING
    assert dryrun.run_id == run.id
    assert run.dry_run_id == dryrun.id
    assert run.dry_run_playbook_id == dryrun.playbook_id
    assert repository.validated == (
        "org_one",
        "playbook_one",
        "version_one",
        "environment_test",
        "credential_test",
    )
    assert workflow.command is not None and workflow.command.dry_run == dryrun
    assert workflow.command.trigger.source == "playbook-dryrun"


def test_completed_rotation_is_the_only_source_of_a_passed_dryrun() -> None:
    current = DryRun(
        id="dryrun_one",
        organisation_id="org_one",
        playbook_id="playbook_one",
        version_id="version_one",
        run_id="run_one",
        status=DryRunStatus.RUNNING,
        environment_id="environment_test",
        credential_id="credential_test",
        requested_by="actor_one",
        started_at=NOW,
    )
    proof = StageProof(
        run_id="run_one",
        organisation_id="org_one",
        stage=Stage.COMPLETE,
        checks=frozenset({"audit-complete"}),
        evidence_ids=("evidence_one",),
        actor_id="coordinator_one",
        recorded_at=NOW + timedelta(minutes=4),
    )
    run = RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="credential_test",
        trigger=_trigger(),
        policy_version="policy_one",
        dry_run_id=current.id,
        dry_run_playbook_id=current.playbook_id,
        stage=Stage.COMPLETE,
        status=RunStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=4),
    )

    changed = _advance_dryrun(current, run, proof)

    assert changed.status is DryRunStatus.PASSED
    assert changed.evidence_ids == ("evidence_one",)
    assert changed.completed_at == run.updated_at


def _trigger() -> Trigger:
    return Trigger(
        source="playbook-dryrun",
        event_id="dryrun_one",
        actor_id="actor_one",
        reason="sandbox validation",
        urgency="planned",
        received_at=NOW,
    )
