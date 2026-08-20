import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    Connection,
    CreateRunCommand,
    DryRun,
    DryRunStatus,
    ExecutionMethod,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookState,
    PlaybookVersion,
    RotationRun,
    Stage,
    Trigger,
)
from policy import digest

from core.errors import PlaybookError
from core.playbook.validate import validate_assignment_connections
from core.storage.repository import MutationResult

_LIST_SCAN_LIMIT = 500


class DryRunWorkflow(Protocol):
    async def create(self, command: CreateRunCommand) -> MutationResult: ...

    async def get(self, organisation_id: str, run_id: str) -> RotationRun: ...


class ConnectionLookup(Protocol):
    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...


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

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]: ...

    async def get_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion: ...

    async def get_dryrun(
        self, organisation_id: str, playbook_id: str, dryrun_id: str
    ) -> DryRun | None: ...

    async def validate_dryrun(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        environment_id: str,
        credential_id: str,
    ) -> None: ...

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

    async def get_assignment(
        self, organisation_id: str, credential_id: str
    ) -> PlaybookAssignment | None: ...


class PlaybookService:
    def __init__(
        self,
        repository: PlaybookRepository,
        clock: Callable[[], datetime],
        workflow: DryRunWorkflow | None = None,
        inventory: ConnectionLookup | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._workflow = workflow
        self._inventory = inventory

    async def list_playbooks(self, organisation_id: str, limit: int = 100) -> tuple[Playbook, ...]:
        playbooks = await self._repository.list_playbooks(organisation_id, _LIST_SCAN_LIMIT)
        ordered = sorted(
            playbooks, key=lambda playbook: (playbook.created_at, playbook.id), reverse=True
        )
        return tuple(ordered[:limit])

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

    async def start_dryrun(
        self,
        organisation_id: str,
        playbook_id: str,
        dryrun_id: str,
        version_id: str,
        environment_id: str,
        credential_id: str,
        policy_version: str,
        actor_id: str,
        command_id: str,
        reason: str,
        urgency: str,
        received_at: datetime,
    ) -> tuple[DryRun, RotationRun, bool]:
        if self._workflow is None:
            raise RuntimeError("playbook dry-run workflow is not configured")
        existing = await self._repository.get_dryrun(organisation_id, playbook_id, dryrun_id)
        if existing is not None:
            expected = (
                organisation_id,
                playbook_id,
                version_id,
                environment_id,
                credential_id,
                actor_id,
            )
            actual = (
                existing.organisation_id,
                existing.playbook_id,
                existing.version_id,
                existing.environment_id,
                existing.credential_id,
                existing.requested_by,
            )
            if actual != expected:
                raise PlaybookError("dry-run ID is already bound to another request")
            run = await self._workflow.get(organisation_id, existing.run_id)
            request_binding = (
                policy_version,
                actor_id,
                reason,
                urgency,
                received_at,
            )
            stored_binding = (
                run.policy_version,
                run.trigger.actor_id,
                run.trigger.reason,
                run.trigger.urgency,
                run.trigger.received_at,
            )
            if stored_binding != request_binding:
                raise PlaybookError("dry-run request changed after it was accepted")
            return existing, run, False
        await self._repository.validate_dryrun(
            organisation_id,
            playbook_id,
            version_id,
            environment_id,
            credential_id,
        )
        run_id = f"run_{hashlib.sha256(command_id.encode()).hexdigest()[:40]}"
        dryrun = DryRun(
            id=dryrun_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            version_id=version_id,
            run_id=run_id,
            status=DryRunStatus.PENDING,
            environment_id=environment_id,
            credential_id=credential_id,
            requested_by=actor_id,
            started_at=received_at,
        )
        result = await self._workflow.create(
            CreateRunCommand(
                id=command_id,
                organisation_id=organisation_id,
                credential_id=credential_id,
                policy_version=policy_version,
                run_id=run_id,
                dry_run=dryrun,
                trigger=Trigger(
                    source="playbook-dryrun",
                    event_id=dryrun_id,
                    actor_id=actor_id,
                    reason=reason,
                    urgency=urgency,
                    received_at=received_at,
                ),
            )
        )
        return dryrun, result.run, result.applied

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
        dry_run_only: bool = False,
        environment_id: str | None = None,
    ) -> PlaybookAssignment:
        version = await self._repository.get_version(organisation_id, playbook_id, version_id)
        if len(set(connection_ids)) != len(connection_ids) or set(connection_ids) != set(
            version.definition.required_connections
        ):
            raise PlaybookError("assignment connections must match the immutable playbook version")
        if self._inventory is not None:
            connections = tuple(
                [
                    await self._inventory.get_connection(organisation_id, item)
                    for item in connection_ids
                ]
            )
            validate_assignment_connections(
                version.definition.execution,
                connections,
                version.definition.allowed_domains,
                version.definition.provider,
            )
        assignment = PlaybookAssignment(
            id=f"assignment_{credential_id}",
            organisation_id=organisation_id,
            credential_id=credential_id,
            playbook_id=playbook_id,
            version_id=version_id,
            connection_ids=connection_ids,
            dry_run_only=dry_run_only,
            environment_id=environment_id,
            assigned_by=actor_id,
            assigned_at=self._clock(),
        )
        return await self._repository.assign(assignment)

    async def get_assignment(
        self, organisation_id: str, credential_id: str
    ) -> PlaybookAssignment | None:
        return await self._repository.get_assignment(organisation_id, credential_id)


def validate_definition(definition: PlaybookDraft) -> None:
    if len(set(definition.required_connections)) != len(definition.required_connections):
        raise PlaybookError("playbook required connections must be unique")
    used_tools = {step.tool for step in definition.steps}
    undeclared = used_tools.difference(definition.allowed_tools)
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise PlaybookError(f"playbook uses undeclared tools: {names}")
    required = {
        Stage.CREATE,
        Stage.STORE,
        Stage.DEPLOY,
        Stage.VERIFY,
        Stage.ROLLOUT,
        Stage.OBSERVE,
        Stage.REVOKE,
    }
    missing = required.difference(step.stage for step in definition.steps)
    if missing:
        names = ", ".join(sorted(stage.value for stage in missing))
        raise PlaybookError(f"playbook is missing lifecycle stages: {names}")
    revoke = tuple(step for step in definition.steps if step.stage is Stage.REVOKE)
    if not revoke or not all(step.protected for step in revoke):
        raise PlaybookError("every revocation step must be protected")
    recoverable = {
        Stage.CREATE,
        Stage.STORE,
        Stage.DEPLOY,
        Stage.VERIFY,
        Stage.ROLLOUT,
        Stage.OBSERVE,
        Stage.REVOKE,
    }
    invalid_recovery = set(definition.recovery).difference(stage.value for stage in Stage)
    missing_recovery = recoverable.difference(
        Stage(name) for name in definition.recovery if name in {stage.value for stage in Stage}
    )
    if invalid_recovery:
        names = ", ".join(sorted(invalid_recovery))
        raise PlaybookError(f"playbook has invalid recovery stages: {names}")
    if missing_recovery:
        names = ", ".join(sorted(stage.value for stage in missing_recovery))
        raise PlaybookError(f"playbook is missing recovery branches: {names}")
    if definition.execution is ExecutionMethod.COMPUTER:
        create = tuple(step for step in definition.steps if step.stage is Stage.CREATE)
        if not any(step.protected for step in create):
            raise PlaybookError("computer-use credential creation must be protected")
