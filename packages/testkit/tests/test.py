from contracts import RunStatus, Stage
from testkit import make_proof, make_run


def test_factories_create_valid_contracts() -> None:
    run = make_run()
    proof = make_proof(Stage.TRIGGER)

    assert run.status is RunStatus.PENDING
    assert proof.stage is Stage.TRIGGER
