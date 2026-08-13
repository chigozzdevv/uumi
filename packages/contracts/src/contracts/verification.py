from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class ProbeKind(StrEnum):
    HTTP = "http"
    EMAIL = "email"
    PROVIDER = "provider"
    SECRET = "secret-store"
    RUNTIME = "runtime"
    TELEMETRY = "telemetry"
    GENERATION = "generation"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ProbeDefinition(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: ProbeKind
    connection_id: Identifier
    target: str = Field(min_length=1, max_length=1024)
    method: str = Field(default="GET", min_length=1, max_length=16)
    headers: dict[str, str] = Field(default_factory=dict)
    body_reference: str | None = Field(default=None, max_length=1024)
    expected_status: tuple[int, ...] = (200,)
    expected_generation_id: Identifier | None = None
    required_fields: dict[str, str | int | bool] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    negative: bool = False


class ProbeResult(Contract):
    probe_id: Identifier
    status: VerificationStatus
    observed_status: int | None = None
    generation_id: Identifier | None = None
    checks: frozenset[str] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    error: str | None = Field(default=None, max_length=1024)
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_error(self) -> "ProbeResult":
        if self.status is VerificationStatus.FAILED and not self.error:
            raise ValueError("failed probes require an error")
        return self


class VerificationReport(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    generation_id: Identifier
    status: VerificationStatus
    results: tuple[ProbeResult, ...] = Field(min_length=1)
    checks: frozenset[str] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_summary(self) -> "VerificationReport":
        statuses = {result.status for result in self.results}
        if self.status is VerificationStatus.PASSED and statuses != {VerificationStatus.PASSED}:
            raise ValueError("a passed report cannot contain non-passing probes")
        return self
