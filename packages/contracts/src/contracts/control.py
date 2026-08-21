from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.incident import Confidence
from contracts.recovery import RecoveryBranch, RecoveryMode
from contracts.state import Stage


class ControlPreferences(Contract):
    automatic_triggers: frozenset[str] = Field(min_length=1)
    rotate_before_expiry_seconds: int = Field(ge=300, le=7776000)
    maximum_observation_seconds: int = Field(ge=60, le=604800)

    @model_validator(mode="after")
    def validate_triggers(self) -> "ControlPreferences":
        supported = {"expiry", "drift", "verified-exposure"}
        if not self.automatic_triggers.issubset(supported):
            raise ValueError("controls contain an unsupported automatic trigger")
        return self


class ControlDefinition(Contract):
    required_checks: dict[Stage, frozenset[str]] = Field(min_length=12)
    allowed_tools: frozenset[str] = Field(min_length=1)
    protected_tools: frozenset[str] = frozenset()
    allowed_recovery_modes: frozenset[RecoveryMode] = Field(min_length=1)
    maximum_observation_seconds: int = Field(ge=60, le=604800)
    preserve_old_generation: bool = True
    require_functional_probe: bool = True
    require_generation_telemetry: bool = True
    rotate_before_expiry_seconds: int = Field(default=604800, ge=300, le=7776000)
    maximum_metadata_age_seconds: int = Field(default=86400, ge=300, le=2592000)
    require_runtime_alignment: bool = True
    automatic_triggers: frozenset[str] = frozenset()
    emergency_triggers: frozenset[str] = frozenset()
    minimum_automatic_confidence: Confidence = Confidence.VERIFIED
    probe_versions: dict[Stage, tuple[Identifier, ...]] = Field(default_factory=dict)
    recovery: dict[Stage, RecoveryBranch] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_coverage(self) -> "ControlDefinition":
        if set(self.required_checks) != set(Stage):
            raise ValueError("controls must define checks for all twelve stages")
        from policy.rules import REQUIRED_CHECKS

        mandatory = dict(REQUIRED_CHECKS)
        if not self.require_functional_probe:
            mandatory[Stage.VERIFY] = mandatory[Stage.VERIFY].difference(
                {"functional-valid", "downstream-valid"}
            )
        if not self.require_generation_telemetry:
            mandatory[Stage.VERIFY] = mandatory[Stage.VERIFY].difference({"telemetry-healthy"})
            mandatory[Stage.OBSERVE] = mandatory[Stage.OBSERVE].difference(
                {"telemetry-healthy", "old-use-clear"}
            )
        for stage, checks in mandatory.items():
            missing = checks.difference(self.required_checks[stage])
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(f"controls omit mandatory {stage.value} checks: {names}")
        if not self.protected_tools.issubset(self.allowed_tools):
            raise ValueError("protected tools must also be allowed")
        if self.require_generation_telemetry and "verification.run" not in self.allowed_tools:
            raise ValueError("generation telemetry controls require verification.run")
        if not self.emergency_triggers.issubset(self.automatic_triggers):
            raise ValueError("emergency triggers must also be automatic triggers")
        return self


class ControlVersion(Contract):
    id: Identifier
    organisation_id: Identifier
    credential_id: Identifier
    number: int = Field(gt=0)
    definition: ControlDefinition
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_by: Identifier
    created_at: AwareDatetime
