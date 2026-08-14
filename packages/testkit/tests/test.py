from contracts import RunStatus, Stage
from policy import digest
from testkit import make_policy_version, make_proof, make_run


def test_factories_create_valid_contracts() -> None:
    run = make_run()
    proof = make_proof(Stage.TRIGGER)
    policy = make_policy_version(run.organisation_id, run.policy_version)

    assert run.status is RunStatus.PENDING
    assert proof.stage is Stage.TRIGGER
    assert policy.digest == digest(policy.definition)
