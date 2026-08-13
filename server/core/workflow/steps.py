from collections.abc import Callable
from datetime import UTC, datetime

from contracts import (
    CleanupRunCommand,
    CompleteRecoveryCommand,
    CompleteStageCommand,
    CreateRunCommand,
    EventKind,
    FailRunCommand,
    PauseRunCommand,
    RecoverRunCommand,
    RenewLeaseCommand,
    ResumeRunCommand,
    RotationRun,
    StartRunCommand,
)

from core.ids import new_id
from core.state import RotationMachine
from core.storage import MutationResult, RunRepository
from core.workflow.trigger import build_run

Clock = Callable[[], datetime]
IdFactory = Callable[[str], str]


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunWorkflow:
    def __init__(
        self,
        repository: RunRepository,
        machine: RotationMachine | None = None,
        clock: Clock = utcnow,
        id_factory: IdFactory = new_id,
    ) -> None:
        self._repository = repository
        self._machine = machine or RotationMachine()
        self._clock = clock
        self._id_factory = id_factory

    async def create(self, command: CreateRunCommand) -> MutationResult:
        now = self._clock()
        run = build_run(command, command.run_id or self._id_factory("run"), now)
        return await self._repository.create(run, command)

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        return await self._repository.get(organisation_id, run_id)

    async def start(self, command: StartRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_STARTED,
            lambda run: self._machine.start(
                run,
                command.owner_id,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def renew(self, command: RenewLeaseCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.LEASE_RENEWED,
            lambda run: self._machine.renew(
                run,
                command.owner_id,
                command.fencing_token,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def complete(self, command: CompleteStageCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.STAGE_COMPLETED,
            lambda run: self._machine.complete(
                run,
                command.proof,
                command.fencing_token,
                command.expected_revision,
                now,
                command.bindings,
            ),
            command.proof,
        )

    async def pause(self, command: PauseRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_PAUSED,
            lambda run: self._machine.pause(
                run,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def resume(self, command: ResumeRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_RESUMED,
            lambda run: self._machine.resume(
                run,
                command.owner_id,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def cleanup(self, command: CleanupRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.CLEANUP_REQUIRED,
            lambda run: self._machine.cleanup(
                run,
                command.failure,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def fail(self, command: FailRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_FAILED,
            lambda run: self._machine.fail(
                run,
                command.failure,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def recover(self, command: RecoverRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RECOVERY_STARTED,
            lambda run: self._machine.recover(
                run,
                command.owner_id,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def complete_recovery(self, command: CompleteRecoveryCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RECOVERY_COMPLETED,
            lambda run: self._machine.complete_recovery(
                run,
                command.recovery_id,
                command.mode,
                command.evidence_ids,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )
