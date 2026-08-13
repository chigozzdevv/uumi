from enum import StrEnum

from pydantic import Field, model_validator

from contracts.base import Contract


class MutationMode(StrEnum):
    NATIVE = "native-idempotency"
    RECONCILABLE = "reconcilable"
    COMPENSATABLE = "compensatable-non-idempotent"


class MutationSemantics(Contract):
    mode: MutationMode
    supports_token: bool = False
    secret_retrievable: bool = False
    deterministic_lookup: tuple[str, ...] = ()
    orphan_evidence: tuple[str, ...] = ()
    compensation: str | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "MutationSemantics":
        if self.mode is MutationMode.NATIVE and not self.supports_token:
            raise ValueError("native idempotency requires a provider token")
        if self.mode is MutationMode.RECONCILABLE and not self.deterministic_lookup:
            raise ValueError("reconcilable mutations require deterministic lookup")
        if self.mode is MutationMode.COMPENSATABLE and not self.compensation:
            raise ValueError("compensatable mutations require a compensation action")
        return self


class ConnectorCapabilities(Contract):
    lists_metadata: bool
    creates_parallel: bool
    controls_scopes: bool
    sets_expiry: bool
    sets_networks: bool
    verifies_status: bool
    reports_usage: bool
    disables: bool
    revokes: bool
    rolls_immediately: bool
    supports_api: bool
    supports_browser: bool
    mutation: MutationSemantics
    version: str = Field(min_length=1, max_length=64)
