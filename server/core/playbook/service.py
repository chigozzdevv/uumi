from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    DryRun,
    DryRunStatus,
    ExecutionMethod,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookState,
    PlaybookVersion,
    Stage,
)
from policy import digest

from core.errors import PlaybookError


class PlaybookRepository(Protocol):
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
    ) -> tuple[Playbook, PlaybookVersion]: ...

    async def get_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion: ...

    async def save_dryrun(self, result: DryRun) -> DryRun: ...

    async def activate(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        dryrun_id: str,
        actor_id: str,
        activated_at: datetime,
    ) -> PlaybookVersion: ...

    async def assign(self, assignment: PlaybookAssignment) -> PlaybookAssignment: ...


class PlaybookService:
    def __init__(
        self,
        repository: PlaybookRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        definition: PlaybookDraft,
        actor_id: str,
        source_ids: tuple[str, ...] = (),
    ) -> tuple[Playbook, PlaybookVersion]:
        validate_definition(definition)
        return await self._repository.add_version(
            playbook_id,
            version_id,
            organisation_id,
            definition,
            digest(definition),
            actor_id,
            self._clock(),
            source_ids,
        )

    async def record_dryrun(self, result: DryRun) -> DryRun:
        version = await self._repository.get_version(
            result.organisation_id,
            result.playbook_id,
            result.version_id,
        )
        if version.state in {PlaybookState.ACTIVE, PlaybookState.SUPERSEDED}:
            raise PlaybookError("immutable active playbook versions cannot be retested")
        if result.status not in {DryRunStatus.PASSED, DryRunStatus.FAILED}:
            raise PlaybookError("only terminal dry-run results can be recorded")
        return await self._repository.save_dryrun(result)

    async def activate(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        dryrun_id: str,
        actor_id: str,
    ) -> PlaybookVersion:
        version = await self._repository.get_version(
            organisation_id,
            playbook_id,
            version_id,
        )
        validate_definition(version.definition)
        if digest(version.definition) != version.digest:
            raise PlaybookError("playbook definition digest does not match its immutable version")
        if version.state is not PlaybookState.APPROVAL or version.dry_run_id != dryrun_id:
            raise PlaybookError("playbook activation requires its recorded passed dry run")
        return await self._repository.activate(
            organisation_id,
            playbook_id,
            version_id,
            dryrun_id,
            actor_id,
            self._clock(),
        )

    async def assign(
        self,
        organisation_id: str,
        credential_id: str,
        playbook_id: str,
        version_id: str,
        connection_ids: tuple[str, ...],
        actor_id: str,
    ) -> PlaybookAssignment:
        assignment = PlaybookAssignment(
            id=f"assignment_{credential_id}",
            organisation_id=organisation_id,
            credential_id=credential_id,
            playbook_id=playbook_id,
            version_id=version_id,
            connection_ids=connection_ids,
            assigned_by=actor_id,
            assigned_at=self._clock(),
        )
        return await self._repository.assign(assignment)


def validate_definition(definition: PlaybookDraft) -> None:
    used_tools = {step.tool for step in definition.steps}
    undeclared = used_tools.difference(definition.allowed_tools)
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise PlaybookError(f"playbook uses undeclared tools: {names}")
    required = {Stage.CREATE, Stage.STORE, Stage.DEPLOY, Stage.VERIFY, Stage.REVOKE}
    missing = required.difference(step.stage for step in definition.steps)
    if missing:
        names = ", ".join(sorted(stage.value for stage in missing))
        raise PlaybookError(f"playbook is missing lifecycle stages: {names}")
    revoke = tuple(step for step in definition.steps if step.stage is Stage.REVOKE)
    if not revoke or not all(step.protected for step in revoke):
        raise PlaybookError("every revocation step must be protected")
    if not definition.recovery:
        raise PlaybookError("playbook requires explicit recovery branches")
    if definition.execution is ExecutionMethod.COMPUTER:
        create = tuple(step for step in definition.steps if step.stage is Stage.CREATE)
        if not any(step.protected for step in create):
            raise PlaybookError("computer-use credential creation must be protected")
