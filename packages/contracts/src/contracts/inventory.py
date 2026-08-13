from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class ConnectionKind(StrEnum):
    PROVIDER = "provider"
    SECRET = "secret-store"
    RUNTIME = "runtime"
    TELEMETRY = "telemetry"
    INCIDENT = "incident"
    BROWSER = "browser"


class ConnectionStatus(StrEnum):
    READY = "ready"
    REAUTHENTICATION = "reauthentication-required"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class Connection(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: ConnectionKind
    provider: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    auth_reference: str | None = Field(default=None, max_length=1024)
    capabilities: frozenset[str] = Field(min_length=1)
    allowed_resources: tuple[str, ...] = Field(min_length=1)
    status: ConnectionStatus
    region: str = Field(min_length=3, max_length=32)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_authentication(self) -> "Connection":
        if self.status is ConnectionStatus.READY and not self.auth_reference:
            raise ValueError("a ready connection requires an authentication reference")
        return self


class Application(Contract):
    id: Identifier
    organisation_id: Identifier
    display_name: str = Field(min_length=1, max_length=160)
    repository_ids: tuple[str, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)


class Environment(Contract):
    id: Identifier
    organisation_id: Identifier
    application_id: Identifier
    display_name: str = Field(min_length=1, max_length=160)
    production: bool
    region: str = Field(min_length=3, max_length=32)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)


class ConsumerService(Contract):
    id: Identifier
    organisation_id: Identifier
    application_id: Identifier
    environment_id: Identifier
    runtime_connection_id: Identifier
    runtime_resource: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=160)
    repository: str | None = Field(default=None, max_length=256)
    identity: str = Field(min_length=1, max_length=512)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)
