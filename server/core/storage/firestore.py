from collections.abc import Mapping
from typing import Any

from contracts import (
    CreateRunCommand,
    DryRun,
    DryRunStatus,
    Environment,
    EventKind,
    PlaybookAssignment,
    PlaybookState,
    PlaybookVersion,
    RotationRun,
    RunCommand,
    RunStatus,
    RunStep,
    StageProof,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.errors import (
    ActiveRunConflictError,
    IdempotencyConflictError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
)
from core.storage.codec import encode
from core.storage.paths import FirestorePaths
from core.storage.repository import (
    MutationResult,
    Transition,
    build_outbox,
    creation_hash,
    mutation_hash,
    resolved_event,
    validate_create,
    validate_transition,
)


class FirestoreRunRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(
        self,
        run: RotationRun,
        command: CreateRunCommand,
    ) -> MutationResult:
        validate_create(run, command)
        run_ref = self._client.document(FirestorePaths.run(run.organisation_id, run.id))
        step_ref = self._client.document(
            FirestorePaths.step(run.organisation_id, run.id, command.id)
        )
        outbox_ref = self._client.document(FirestorePaths.outbox(run.organisation_id, command.id))
        lock_ref = self._client.document(
            FirestorePaths.lock(run.organisation_id, run.credential_id)
        )
        dedupe_ref = self._client.document(
            FirestorePaths.dedupe(
                run.organisation_id,
                run.trigger.source,
                run.trigger.event_id,
            )
        )
        request_hash = creation_hash(command)
        dryrun_ref = (
            self._client.document(
                FirestorePaths.dryrun(
                    command.dry_run.organisation_id,
                    command.dry_run.playbook_id,
                    command.dry_run.id,
                )
            )
            if command.dry_run is not None
            else None
        )
        dryrun_version_ref = (
            self._client.document(
                FirestorePaths.playbook_version(
                    command.dry_run.organisation_id,
                    command.dry_run.playbook_id,
                    command.dry_run.version_id,
                )
            )
            if command.dry_run is not None
            else None
        )
        dryrun_assignment_ref = (
            self._client.document(
                FirestorePaths.assignment(
                    command.dry_run.organisation_id, command.dry_run.credential_id
                )
            )
            if command.dry_run is not None
            else None
        )
        dryrun_environment_ref = (
            self._client.document(
                FirestorePaths.environment(
                    command.dry_run.organisation_id, command.dry_run.environment_id
                )
            )
            if command.dry_run is not None
            else None
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> MutationResult:
            dedupe = await dedupe_ref.get(transaction=transaction)
            if dedupe.exists:
                return await self._existing_create(
                    transaction,
                    dedupe,
                    run.organisation_id,
                    request_hash,
                )

            lock = await lock_ref.get(transaction=transaction)
            if lock.exists:
                locked_run = _required_data(lock).get("run_id", "unknown")
                raise ActiveRunConflictError(
                    f"credential {run.credential_id} is already owned by run {locked_run}"
                )

            existing = await run_ref.get(transaction=transaction)
            if existing.exists:
                raise IdempotencyConflictError(f"run id {run.id} already exists")
            existing_event = await outbox_ref.get(transaction=transaction)
            if existing_event.exists:
                raise IdempotencyConflictError(
                    f"command {command.id} already belongs to another run"
                )
            if (
                dryrun_ref is not None
                and dryrun_version_ref is not None
                and dryrun_assignment_ref is not None
                and dryrun_environment_ref is not None
                and command.dry_run is not None
            ):
                existing_dryrun = await dryrun_ref.get(transaction=transaction)
                version_snapshot = await dryrun_version_ref.get(transaction=transaction)
                assignment_snapshot = await dryrun_assignment_ref.get(transaction=transaction)
                environment_snapshot = await dryrun_environment_ref.get(transaction=transaction)
                if existing_dryrun.exists:
                    raise IdempotencyConflictError(f"dry run {command.dry_run.id} already exists")
                if not all(
                    item.exists
                    for item in (version_snapshot, assignment_snapshot, environment_snapshot)
                ):
                    raise StorageIntegrityError("dry-run isolation inputs are missing")
                version = PlaybookVersion.model_validate(_required_data(version_snapshot))
                assignment = PlaybookAssignment.model_validate(_required_data(assignment_snapshot))
                environment = Environment.model_validate(_required_data(environment_snapshot))
                if version.state is not PlaybookState.TEST:
                    raise StorageIntegrityError("dry-run playbook version is not awaiting a test")
                if (
                    command.dry_run.status is not DryRunStatus.PENDING
                    or command.dry_run.evidence_ids
                    or command.dry_run.checks
                    or command.dry_run.failure is not None
                    or command.dry_run.completed_at is not None
                    or environment.production
                    or not assignment.dry_run_only
                    or assignment.environment_id != environment.id
                    or assignment.credential_id != run.credential_id
                    or assignment.playbook_id != command.dry_run.playbook_id
                    or assignment.version_id != command.dry_run.version_id
                ):
                    raise StorageIntegrityError("dry-run isolation binding is invalid")

            step = RunStep(
                id=command.id,
                organisation_id=run.organisation_id,
                run_id=run.id,
                operation=command.operation,
                command_hash=request_hash,
                actor_id=command.trigger.actor_id,
                after_stage=run.stage,
                after_status=run.status,
                revision=run.revision,
                recorded_at=run.created_at,
            )
            event = build_outbox(
                command.id,
                run,
                EventKind.RUN_CREATED,
                command.trigger.actor_id,
                run.created_at,
                {
                    "source": run.trigger.source,
                    "urgency": run.trigger.urgency,
                },
            )

            transaction.set(run_ref, encode(run))
            if dryrun_ref is not None and command.dry_run is not None:
                transaction.create(dryrun_ref, encode(command.dry_run))
            transaction.set(step_ref, encode(step))
            transaction.set(outbox_ref, encode(event))
            transaction.set(
                lock_ref,
                {
                    "organisation_id": run.organisation_id,
                    "credential_id": run.credential_id,
                    "run_id": run.id,
                    "created_at": run.created_at,
                },
            )
            transaction.set(
                dedupe_ref,
                {
                    "organisation_id": run.organisation_id,
                    "run_id": run.id,
                    "step_id": command.id,
                    "request_hash": request_hash,
                    "created_at": run.created_at,
                },
            )
            return MutationResult(run=run, step=step, applied=True)

        return await apply(self._client.transaction(max_attempts=5))

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        reference = self._client.document(FirestorePaths.run(organisation_id, run_id))
        snapshot = await reference.get()
        if not snapshot.exists:
            raise RunNotFoundError(f"run {run_id} was not found")
        run = RotationRun.model_validate(_required_data(snapshot))
        _tenant(run, organisation_id)
        return run

    async def mutate(
        self,
        command: RunCommand,
        kind: EventKind,
        transition: Transition,
        proof: StageProof | None = None,
    ) -> MutationResult:
        run_ref = self._client.document(FirestorePaths.run(command.organisation_id, command.run_id))
        step_ref = self._client.document(
            FirestorePaths.step(command.organisation_id, command.run_id, command.id)
        )
        outbox_ref = self._client.document(
            FirestorePaths.outbox(command.organisation_id, command.id)
        )
        request_hash = mutation_hash(command)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> MutationResult:
            existing_step = await step_ref.get(transaction=transaction)
            if existing_step.exists:
                step = RunStep.model_validate(_required_data(existing_step))
                if step.command_hash != request_hash:
                    raise IdempotencyConflictError(
                        f"command {command.id} was already used for another mutation"
                    )
                current = await run_ref.get(transaction=transaction)
                if not current.exists:
                    raise StorageIntegrityError(
                        f"run {command.run_id} disappeared after command execution"
                    )
                run = RotationRun.model_validate(_required_data(current))
                _tenant(run, command.organisation_id)
                return MutationResult(run=run, step=step, applied=False)

            existing_event = await outbox_ref.get(transaction=transaction)
            if existing_event.exists:
                raise IdempotencyConflictError(
                    f"command {command.id} already belongs to another run"
                )

            current = await run_ref.get(transaction=transaction)
            if not current.exists:
                raise RunNotFoundError(f"run {command.run_id} was not found")
            run = RotationRun.model_validate(_required_data(current))
            _tenant(run, command.organisation_id)
            if run.revision != command.expected_revision:
                raise RevisionConflictError(
                    f"expected revision {command.expected_revision}, found {run.revision}"
                )

            lock_ref = self._client.document(
                FirestorePaths.lock(command.organisation_id, run.credential_id)
            )
            lock = await lock_ref.get(transaction=transaction)
            if not lock.exists or _required_data(lock).get("run_id") != run.id:
                raise StorageIntegrityError(f"run {run.id} does not hold its credential lock")

            dryrun_ref = None
            version_ref = None
            dryrun = None
            version = None
            if run.dry_run_id is not None and run.dry_run_playbook_id is not None:
                dryrun_ref = self._client.document(
                    FirestorePaths.dryrun(
                        run.organisation_id, run.dry_run_playbook_id, run.dry_run_id
                    )
                )
                dryrun_snapshot = await dryrun_ref.get(transaction=transaction)
                if not dryrun_snapshot.exists:
                    raise StorageIntegrityError("rotation run lost its dry-run record")
                dryrun = DryRun.model_validate(_required_data(dryrun_snapshot))
                version_ref = self._client.document(
                    FirestorePaths.playbook_version(
                        run.organisation_id, dryrun.playbook_id, dryrun.version_id
                    )
                )
                version_snapshot = await version_ref.get(transaction=transaction)
                if not version_snapshot.exists:
                    raise StorageIntegrityError("dry-run playbook version disappeared")
                version = PlaybookVersion.model_validate(_required_data(version_snapshot))

            updated = transition(run)
            validate_transition(run, updated, command.organisation_id)

            recorded_at = updated.updated_at
            step = RunStep(
                id=command.id,
                organisation_id=command.organisation_id,
                run_id=run.id,
                operation=command.operation,
                command_hash=request_hash,
                actor_id=command.actor_id,
                before_stage=run.stage,
                after_stage=updated.stage,
                before_status=run.status,
                after_status=updated.status,
                revision=updated.revision,
                proof=proof,
                recorded_at=recorded_at,
            )
            resolved_kind = resolved_event(kind, updated)
            event = build_outbox(
                command.id,
                updated,
                resolved_kind,
                command.actor_id,
                recorded_at,
                {
                    "operation": command.operation,
                    "previous_revision": run.revision,
                },
            )

            transaction.set(run_ref, encode(updated))
            if dryrun_ref is not None and dryrun is not None:
                changed_dryrun = _advance_dryrun(dryrun, updated, proof)
                transaction.set(dryrun_ref, encode(changed_dryrun))
                if updated.status is RunStatus.COMPLETED:
                    if version_ref is None or version is None:
                        raise StorageIntegrityError("dry-run completion lost its playbook version")
                    if version.id != dryrun.version_id or version.state is not PlaybookState.TEST:
                        raise StorageIntegrityError(
                            "dry-run completion cannot advance its playbook version"
                        )
                    transaction.set(
                        version_ref,
                        encode(
                            version.model_copy(
                                update={
                                    "state": PlaybookState.APPROVAL,
                                    "dry_run_id": dryrun.id,
                                }
                            )
                        ),
                    )
            transaction.set(step_ref, encode(step))
            transaction.set(outbox_ref, encode(event))
            if updated.status is RunStatus.COMPLETED:
                transaction.delete(lock_ref)
            return MutationResult(run=updated, step=step, applied=True)

        return await apply(self._client.transaction(max_attempts=5))

    async def _existing_create(
        self,
        transaction: AsyncTransaction,
        dedupe: DocumentSnapshot,
        organisation_id: str,
        request_hash: str,
    ) -> MutationResult:
        data = _required_data(dedupe)
        if data.get("request_hash") != request_hash:
            raise IdempotencyConflictError("source event was already bound to another request")
        run_id = _required_string(data, "run_id")
        step_id = _required_string(data, "step_id")
        run_ref = self._client.document(FirestorePaths.run(organisation_id, run_id))
        step_ref = self._client.document(FirestorePaths.step(organisation_id, run_id, step_id))
        run_snapshot = await run_ref.get(transaction=transaction)
        step_snapshot = await step_ref.get(transaction=transaction)
        if not run_snapshot.exists or not step_snapshot.exists:
            raise StorageIntegrityError("deduplicated run is incomplete")
        run = RotationRun.model_validate(_required_data(run_snapshot))
        step = RunStep.model_validate(_required_data(step_snapshot))
        _tenant(run, organisation_id)
        return MutationResult(run=run, step=step, applied=False)


def _tenant(run: RotationRun, organisation_id: str) -> None:
    if run.organisation_id != organisation_id:
        raise StorageIntegrityError("run organisation does not match its document path")


def _advance_dryrun(
    current: DryRun,
    run: RotationRun,
    proof: StageProof | None,
) -> DryRun:
    if (
        current.run_id != run.id
        or current.organisation_id != run.organisation_id
        or current.credential_id != run.credential_id
    ):
        raise StorageIntegrityError("dry-run lineage changed during rotation")
    if current.status in {DryRunStatus.PASSED, DryRunStatus.FAILED}:
        raise StorageIntegrityError("terminal dry-run evidence cannot be changed")
    checks = current.checks
    evidence_ids = current.evidence_ids
    if proof is not None:
        checks = frozenset((*checks, *proof.checks))
        evidence_ids = tuple(dict.fromkeys((*evidence_ids, *proof.evidence_ids)))
    if run.status is RunStatus.COMPLETED:
        return current.model_copy(
            update={
                "status": DryRunStatus.PASSED,
                "checks": checks,
                "evidence_ids": evidence_ids,
                "failure": None,
                "completed_at": run.updated_at,
            }
        )
    if run.status in {RunStatus.FAILED, RunStatus.CLEANUP}:
        if run.failure is None:
            raise StorageIntegrityError("recoverable dry run has no failure")
        return current.model_copy(
            update={
                "status": DryRunStatus.RECOVERY,
                "checks": checks,
                "evidence_ids": evidence_ids,
                "failure": run.failure.message,
            }
        )
    if run.status is RunStatus.RECOVERING:
        return current.model_copy(
            update={
                "status": DryRunStatus.RUNNING,
                "checks": checks,
                "evidence_ids": evidence_ids,
                "failure": None,
            }
        )
    return current.model_copy(
        update={"status": DryRunStatus.RUNNING, "checks": checks, "evidence_ids": evidence_ids}
    )


def _required_data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"document {snapshot.id} has no data")
    return data


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise StorageIntegrityError(f"stored field {key} is missing or invalid")
    return value
