from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class AuditEvent(Contract):
    id: Identifier
    organisation_id: Identifier
    sequence: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=128)
    actor_id: Identifier
    resource: str = Field(min_length=1, max_length=1024)
    run_id: Identifier | None = None
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    evidence_ids: tuple[Identifier, ...] = ()
    previous_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurred_at: AwareDatetime
    region: str = Field(min_length=3, max_length=32)

    @model_validator(mode="after")
    def validate_genesis(self) -> "AuditEvent":
        if self.sequence == 0 and self.previous_hash != "0" * 64:
            raise ValueError("the first audit event must reference the genesis hash")
        return self


class Evidence(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: str = Field(min_length=1, max_length=96)
    resource: str = Field(min_length=1, max_length=1024)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0)
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    region: str = Field(min_length=3, max_length=32)
