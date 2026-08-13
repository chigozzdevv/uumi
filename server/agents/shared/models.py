from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InventoryAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str
    declared_consumers: list[str] = Field(default_factory=list)
    observed_consumers: list[str] = Field(default_factory=list)
    missing_inventory: list[str] = Field(default_factory=list)
    stale_inventory: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    conclusion: str


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["parallel", "dual-slot", "immediate", "multi-consumer"]
    observation_seconds: int = Field(ge=60, le=604800)
    ordered_stages: list[str] = Field(min_length=12)
    recovery_actions: list[str] = Field(min_length=1)
    rationale: str


class OperatorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    ready: bool
    expected_checkpoint: str
    drift_detected: bool
    pause_reason: str | None = None
