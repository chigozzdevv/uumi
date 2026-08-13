from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Approval,
    ApprovalDecision,
    MutationMode,
    MutationSemantics,
    RotationPlan,
    RotationStrategy,
)
from pydantic import ValidationError

NOW = datetime.now(UTC)
DIGEST = "a" * 64


def test_native_mutation_requires_provider_token() -> None:
    with pytest.raises(ValidationError, match="provider token"):
        MutationSemantics(mode=MutationMode.NATIVE)


def test_compensatable_mutation_requires_cleanup() -> None:
    with pytest.raises(ValidationError, match="compensation action"):
        MutationSemantics(mode=MutationMode.COMPENSATABLE)


def test_rollout_must_be_ordered_and_complete() -> None:
    with pytest.raises(ValidationError, match="unique and increasing"):
        RotationPlan(
            id="plan_one",
            organisation_id="org_one",
            run_id="run_one",
            credential_id="cred_one",
            policy_version="policy_one",
            playbook_version="playbook_one",
            strategy=RotationStrategy.PARALLEL,
            target_scopes=frozenset({"mail.send"}),
            consumer_ids=("service_one",),
            rollout=(25, 5, 100),
            observation_seconds=60,
            recovery_id="recovery_one",
        )


def test_approval_consumption_requires_approved_decision() -> None:
    with pytest.raises(ValidationError, match="only an approved action"):
        Approval(
            id="approval_one",
            organisation_id="org_one",
            run_id="run_one",
            action_id="action_one",
            action_digest=DIGEST,
            plan_hash=DIGEST,
            evidence_hash=DIGEST,
            generation_id="generation_one",
            decision=ApprovalDecision.REJECTED,
            approver_id="user_one",
            expires_at=NOW + timedelta(minutes=10),
            decided_at=NOW,
            consumed_at=NOW,
        )


def test_contracts_are_immutable() -> None:
    mutation = MutationSemantics(
        mode=MutationMode.COMPENSATABLE,
        compensation="revoke-attributable-orphan",
    )

    with pytest.raises(ValidationError, match="frozen"):
        setattr(mutation, "compensation", "retry")  # noqa: B010
