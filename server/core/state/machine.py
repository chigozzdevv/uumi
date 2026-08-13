from datetime import datetime

from contracts import Failure, Lease, RotationRun, RunStatus, Stage, StageProof
from contracts.state import STAGES
from policy import GatePolicy

from core.errors import LeaseConflictError, RevisionConflictError, TransitionRejectedError


class RotationMachine:
    def __init__(self, policy: GatePolicy | None = None) -> None:
        self._policy = policy or GatePolicy()

    def start(
        self,
        run: RotationRun,
        owner_id: str,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status is not RunStatus.PENDING:
            raise TransitionRejectedError("only a pending run can start")
        self._future(expires_at, now)
        token = run.lease.fencing_token + 1 if run.lease else 1
        lease = Lease(owner_id=owner_id, fencing_token=token, expires_at=expires_at)
        return self._update(run, now, status=RunStatus.RUNNING, lease=lease)

    def renew(
        self,
        run: RotationRun,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.lease is None or run.lease.owner_id != owner_id:
            raise LeaseConflictError("only the lease owner can renew it")
        self._future(expires_at, now)
        lease = run.lease.model_copy(update={"expires_at": expires_at})
        return self._update(run, now, lease=lease)

    def complete(
        self,
        run: RotationRun,
        proof: StageProof,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING}:
            raise TransitionRejectedError("a paused or terminal run cannot advance")
        if proof.run_id != run.id or proof.organisation_id != run.organisation_id:
            raise TransitionRejectedError("proof belongs to a different run")
        if proof.stage is not run.stage:
            raise TransitionRejectedError("proof does not match the current stage")
        self._policy.validate(proof)

        if run.stage is Stage.COMPLETE:
            return self._update(
                run,
                now,
                status=RunStatus.COMPLETED,
                lease=None,
            )

        index = STAGES.index(run.stage)
        return self._update(
            run,
            now,
            stage=STAGES[index + 1],
            status=RunStatus.RUNNING,
        )

    def pause(
        self,
        run: RotationRun,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING}:
            raise TransitionRejectedError("only active work can pause")
        return self._update(run, now, status=RunStatus.PAUSED)

    def resume(
        self,
        run: RotationRun,
        owner_id: str,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status is not RunStatus.PAUSED:
            raise TransitionRejectedError("only a paused run can resume")
        if run.lease and run.lease.expires_at > now and run.lease.owner_id != owner_id:
            raise LeaseConflictError("an active lease belongs to another worker")
        self._future(expires_at, now)
        token = run.lease.fencing_token + 1 if run.lease else 1
        lease = Lease(owner_id=owner_id, fencing_token=token, expires_at=expires_at)
        return self._update(run, now, status=RunStatus.RUNNING, lease=lease)

    def cleanup(
        self,
        run: RotationRun,
        failure: Failure,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING, RunStatus.PAUSED}:
            raise TransitionRejectedError("terminal work cannot enter cleanup")
        return self._update(run, now, status=RunStatus.CLEANUP, failure=failure)

    def fail(
        self,
        run: RotationRun,
        failure: Failure,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise TransitionRejectedError("terminal work cannot fail again")
        return self._update(
            run,
            now,
            status=RunStatus.FAILED,
            failure=failure,
            lease=None,
        )

    def recover(
        self,
        run: RotationRun,
        owner_id: str,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status not in {RunStatus.FAILED, RunStatus.CLEANUP, RunStatus.PAUSED}:
            raise TransitionRejectedError("only interrupted work can recover")
        if run.lease and run.lease.expires_at > now and run.lease.owner_id != owner_id:
            raise LeaseConflictError("an active lease belongs to another worker")
        self._future(expires_at, now)
        token = run.lease.fencing_token + 1 if run.lease else 1
        lease = Lease(owner_id=owner_id, fencing_token=token, expires_at=expires_at)
        return self._update(
            run,
            now,
            status=RunStatus.RECOVERING,
            failure=None,
            lease=lease,
        )

    @staticmethod
    def _revision(run: RotationRun, expected: int) -> None:
        if run.revision != expected:
            raise RevisionConflictError(f"expected revision {expected}, found {run.revision}")

    def _control(
        self,
        run: RotationRun,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> None:
        self._revision(run, expected_revision)
        if run.lease is None:
            raise LeaseConflictError("run has no active lease")
        if run.lease.fencing_token != fencing_token:
            raise LeaseConflictError("stale fencing token")
        if run.lease.expires_at <= now:
            raise LeaseConflictError("lease has expired")

    @staticmethod
    def _future(expires_at: datetime, now: datetime) -> None:
        if expires_at <= now:
            raise LeaseConflictError("lease expiry must be in the future")

    @staticmethod
    def _update(run: RotationRun, now: datetime, **changes: object) -> RotationRun:
        payload = run.model_dump()
        payload.update(changes)
        payload["updated_at"] = now
        payload["revision"] = run.revision + 1
        return RotationRun.model_validate(payload)
