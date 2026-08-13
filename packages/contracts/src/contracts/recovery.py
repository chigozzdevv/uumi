from enum import StrEnum

from pydantic import Field

from contracts.base import Contract, Identifier
from contracts.state import Stage


class RecoveryMode(StrEnum):
    RETRY = "retry"
    ROLLBACK = "rollback"
    ROLLFORWARD = "rollforward"
    CLEANUP = "cleanup"
    ESCALATE = "escalate"


class RecoveryPlan(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    failed_stage: Stage
    mode: RecoveryMode
    steps: tuple[str, ...] = Field(min_length=1)
    preserves_old_generation: bool
    requires_approval: bool
