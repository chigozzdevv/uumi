from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MORE_EVIDENCE = "more-evidence"
    EXTEND = "extend-observation"


class Approval(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    action_id: Identifier
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation_id: Identifier
    decision: ApprovalDecision = ApprovalDecision.PENDING
    approver_id: Identifier | None = None
    expires_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    consumed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "Approval":
        decided = self.decision is not ApprovalDecision.PENDING
        if decided != (self.decided_at is not None and self.approver_id is not None):
            raise ValueError("a decision requires an approver and decision time")
        if self.consumed_at is not None and self.decision is not ApprovalDecision.APPROVED:
            raise ValueError("only an approved action can be consumed")
        return self
