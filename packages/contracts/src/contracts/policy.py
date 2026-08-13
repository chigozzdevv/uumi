from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.recovery import RecoveryMode
from contracts.state import Stage


class PolicyState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class PolicyDefinition(Contract):
    required_checks: dict[Stage, frozenset[str]] = Field(min_length=12)
    allowed_tools: frozenset[str] = Field(min_length=1)
    protected_tools: frozenset[str] = frozenset()
    allowed_recovery_modes: frozenset[RecoveryMode] = Field(min_length=1)
    maximum_observation_seconds: int = Field(ge=60, le=604800)
    preserve_old_generation: bool = True
    require_functional_probe: bool = True
    require_generation_telemetry: bool = True

    @model_validator(mode="after")
    def validate_coverage(self) -> "PolicyDefinition":
        if set(self.required_checks) != set(Stage):
            raise ValueError("policy must define checks for all twelve stages")
        if not self.protected_tools.issubset(self.allowed_tools):
            raise ValueError("protected tools must also be allowed")
        if self.require_generation_telemetry and "verification.run" not in self.allowed_tools:
            raise ValueError("generation telemetry policy requires verification.run")
        return self


class PolicyVersion(Contract):
    id: Identifier
    organisation_id: Identifier
    policy_id: Identifier
    number: int = Field(gt=0)
    definition: PolicyDefinition
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: PolicyState
    created_by: Identifier
    created_at: AwareDatetime
    approved_by: Identifier | None = None
    approved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_activation(self) -> "PolicyVersion":
        approved = self.approved_by is not None and self.approved_at is not None
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError("policy approval identity and time must be set together")
        if self.state is PolicyState.ACTIVE and not approved:
            raise ValueError("active policy versions require approval")
        return self


class Policy(Contract):
    id: Identifier
    organisation_id: Identifier
    name: str = Field(min_length=1, max_length=160)
    latest_version: int = Field(default=0, ge=0)
    active_version_id: Identifier | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)
