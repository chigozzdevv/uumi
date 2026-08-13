from datetime import UTC, datetime

import pytest
from contracts import ProtectedAction, Stage, StageProof
from policy import GatePolicy, PolicyViolationError, digest


def test_digest_is_stable_for_parameter_order() -> None:
    first = ProtectedAction(
        id="action_one",
        organisation_id="org_one",
        run_id="run_one",
        kind="revoke",
        resource="sendgrid/key/key-one",
        credential_id="cred_one",
        generation_id="generation_one",
        provider_id="key-one",
        parameters={"reason": "rotation", "force": False},
    )
    second = first.model_copy(update={"parameters": {"force": False, "reason": "rotation"}})

    assert digest(first) == digest(second)


def test_gate_policy_reports_every_missing_check() -> None:
    proof = StageProof(
        run_id="run_one",
        organisation_id="org_one",
        stage=Stage.TRIGGER,
        checks=frozenset({"request-authenticated"}),
        evidence_ids=("evidence_one",),
        actor_id="service_one",
        recorded_at=datetime.now(UTC),
    )

    with pytest.raises(PolicyViolationError) as error:
        GatePolicy().validate(proof)

    assert error.value.missing == frozenset({"source-deduplicated", "lease-held"})
