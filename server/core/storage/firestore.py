from collections.abc import Mapping
from typing import Any

from contracts import (
    CreateRunCommand,
    EventKind,
    PolicyState,
    PolicyVersion,
    RotationRun,
    RunCommand,
    RunStatus,
    RunStep,
    StageProof,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from policy import GatePolicy, digest

from core.errors import (
    ActiveRunConflictError,
    IdempotencyConflictError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
)
from core.storage.catalog import aggregate_count
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
        policy_ref = self._client.document(
            FirestorePaths.policy_version(run.organisation_id, run.policy_version)
        )
        request_hash = creation_hash(command)

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
            policy_snapshot = await policy_ref.get(transaction=transaction)
            if not policy_snapshot.exists or policy_snapshot.to_dict() is None:
                raise StorageIntegrityError("run policy version is missing")
            policy = PolicyVersion.model_validate(policy_snapshot.to_dict())
            if (
                policy.organisation_id != run.organisation_id
                or policy.id != run.policy_version
                or policy.state is not PolicyState.ACTIVE
                or policy.digest != digest(policy.definition)
            ):
                raise StorageIntegrityError("run policy version is not active and immutable")
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

    async def list_runs(self, organisation_id: str, limit: int) -> tuple[RotationRun, ...]:
        path = f"{FirestorePaths.organisation(organisation_id)}/runs"
        runs: list[RotationRun] = []
        async for snapshot in self._client.collection(path).limit(limit).stream():
            run = RotationRun.model_validate(_required_data(snapshot))
            _tenant(run, organisation_id)
            runs.append(run)
        return tuple(runs)

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        path = f"{FirestorePaths.organisation(organisation_id)}/runs"
        query = self._client.collection(path).where(
            "status", "in", sorted(status.value for status in statuses)
        )
        return await aggregate_count(query)

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

            if proof is not None:
                policy_ref = self._client.document(
                    FirestorePaths.policy_version(run.organisation_id, run.policy_version)
                )
                policy_snapshot = await policy_ref.get(transaction=transaction)
                if not policy_snapshot.exists or policy_snapshot.to_dict() is None:
                    raise StorageIntegrityError("run policy version disappeared")
                policy = PolicyVersion.model_validate(policy_snapshot.to_dict())
                if policy.state not in {
                    PolicyState.ACTIVE,
                    PolicyState.SUPERSEDED,
                } or policy.digest != digest(policy.definition):
                    raise StorageIntegrityError("run policy version lost immutable authority")
                GatePolicy(policy.definition.required_checks).validate(proof)

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
            transaction.set(step_ref, encode(step))
            transaction.set(outbox_ref, encode(event))
            if updated.status in {RunStatus.COMPLETED, RunStatus.COMPENSATED}:
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
