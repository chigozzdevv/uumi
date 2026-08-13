from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str
    declared_consumers: list[str] = Field(default_factory=list)
    observed_consumers: list[str] = Field(default_factory=list)
    missing_inventory: list[str] = Field(default_factory=list)
    stale_inventory: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    conclusion: str


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["plan", "recovery", "escalate"]
    strategy: Literal["parallel", "dual-slot", "immediate", "multi-consumer"] | None = None
    observation_seconds: int | None = Field(default=None, ge=60, le=604800)
    ordered_stages: list[str] | None = None
    recovery_actions: list[str] | None = None
    recovery_id: str | None = None
    recovery_mode: Literal["retry", "rollback", "rollforward", "cleanup", "escalate"] | None = None
    eligible: bool | None = None
    rationale: str

    @model_validator(mode="after")
    def validate_branch(self) -> "PlannerOutput":
        if self.decision == "plan":
            if (
                self.strategy is None
                or self.observation_seconds is None
                or self.ordered_stages is None
                or len(self.ordered_stages) < 12
                or not self.recovery_actions
            ):
                raise ValueError("planning output is incomplete")
        elif self.recovery_id is None or self.recovery_mode is None or self.eligible is None:
            raise ValueError("recovery output is incomplete")
        return self


class OperatorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    ready: bool
    expected_checkpoint: str
    drift_detected: bool
    pause_reason: str | None = None
