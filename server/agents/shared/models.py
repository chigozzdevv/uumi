from typing import Annotated, Any, Literal

from contracts import PageCheckpoint, PlaybookDraft, SecureField, Selector, StepOutput
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode


class PlaybookAgentStepBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=1024)
    parameters: dict[str, str | int | bool | tuple[str, ...]] = Field(default_factory=dict)
    protected: bool = False
    evidence_checks: list[str] = Field(min_length=1)
    selectors: tuple[Selector, ...] = Field(min_length=1, max_length=1)
    checkpoint: PageCheckpoint
    outputs: tuple[StepOutput, ...] = ()
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    retry_limit: int = Field(default=0, ge=0, le=5)


class PlaybookAgentBrowserStep(PlaybookAgentStepBase):
    stage: Literal["create", "revoke"]
    tool: Literal["browser.navigate", "browser.click", "browser.fill"]
    effect: Literal["none"] = "none"
    secure_field: None = None


class PlaybookAgentCreateStep(PlaybookAgentStepBase):
    stage: Literal["create"]
    tool: Literal["browser.secure-capture"]
    effect: Literal["create-credential"]
    secure_field: SecureField


class PlaybookAgentRevokeStep(PlaybookAgentStepBase):
    stage: Literal["revoke"]
    tool: Literal["browser.revokeCredential"]
    effect: Literal["revoke-credential"]
    secure_field: None = None


PlaybookAgentStep = Annotated[
    PlaybookAgentBrowserStep | PlaybookAgentCreateStep | PlaybookAgentRevokeStep,
    Field(discriminator="effect"),
]


class PlaybookAgentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=64)
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    steps: tuple[PlaybookAgentStep, ...] = Field(min_length=1)
    login_url_pattern: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_browser_procedure(self) -> "PlaybookAgentDraft":
        PlaybookDraft.model_validate(self.model_dump(mode="json"))
        return self

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        _remove_unique_items(schema)
        return schema


def _remove_unique_items(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("uniqueItems", None)
        for nested in value.values():
            _remove_unique_items(nested)
    elif isinstance(value, list):
        for nested in value:
            _remove_unique_items(nested)


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
