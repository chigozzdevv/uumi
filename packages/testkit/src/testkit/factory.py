from datetime import UTC, datetime

from contracts import RotationRun, Stage, StageProof, Trigger
from policy import GatePolicy


def make_run(now: datetime | None = None) -> RotationRun:
    current = now or datetime.now(UTC)
    return RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="cred_one",
        trigger=Trigger(
            source="schedule",
            event_id="event-one",
            actor_id="service_one",
            reason="routine rotation",
            urgency="routine",
            received_at=current,
        ),
        policy_version="policy_one",
        created_at=current,
        updated_at=current,
    )


def make_proof(stage: Stage, now: datetime | None = None) -> StageProof:
    current = now or datetime.now(UTC)
    return StageProof(
        run_id="run_one",
        organisation_id="org_one",
        stage=stage,
        checks=GatePolicy().checks(stage),
        evidence_ids=(f"evidence_{stage.value}",),
        actor_id="service_one",
        recorded_at=current,
    )
