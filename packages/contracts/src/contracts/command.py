from typing import Literal

from pydantic import AwareDatetime, Field

from contracts.base import Contract, Identifier
from contracts.evidence import StageProof
from contracts.run import Failure, Trigger


class CreateRunCommand(Contract):
    operation: Literal["create"] = "create"
    id: Identifier
    organisation_id: Identifier
    credential_id: Identifier
    policy_version: Identifier
    trigger: Trigger


class RunCommand(Contract):
    operation: str = Field(min_length=1, max_length=96)
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    actor_id: Identifier
    expected_revision: int = Field(ge=0)


class StartRunCommand(RunCommand):
    operation: Literal["start"] = "start"
    owner_id: Identifier
    expires_at: AwareDatetime


class RenewLeaseCommand(RunCommand):
    operation: Literal["renew"] = "renew"
    owner_id: Identifier
    expires_at: AwareDatetime
    fencing_token: int = Field(gt=0)


class CompleteStageCommand(RunCommand):
    operation: Literal["complete-stage"] = "complete-stage"
    fencing_token: int = Field(gt=0)
    proof: StageProof


class PauseRunCommand(RunCommand):
    operation: Literal["pause"] = "pause"
    fencing_token: int = Field(gt=0)


class ResumeRunCommand(RunCommand):
    operation: Literal["resume"] = "resume"
    owner_id: Identifier
    expires_at: AwareDatetime


class FailRunCommand(RunCommand):
    operation: Literal["fail"] = "fail"
    fencing_token: int = Field(gt=0)
    failure: Failure


class CleanupRunCommand(RunCommand):
    operation: Literal["cleanup"] = "cleanup"
    fencing_token: int = Field(gt=0)
    failure: Failure


class RecoverRunCommand(RunCommand):
    operation: Literal["recover"] = "recover"
    owner_id: Identifier
    expires_at: AwareDatetime
