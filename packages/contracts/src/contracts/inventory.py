from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.http import HttpProviderApi


class ConnectionRole(StrEnum):
    PROVIDER = "provider"
    SECRET_STORE = "secret-store"
    RUNTIME = "runtime"
    TELEMETRY = "telemetry"
    INCIDENT = "incident"


class ConnectionInterface(StrEnum):
    API = "api"
    BROWSER = "browser"


class ConnectionAuthorization(StrEnum):
    OAUTH = "oauth"
    WORKLOAD_IDENTITY = "workload-identity"
    API_KEY = "api-key"
    BROWSER_SESSION = "browser-session"


class ConnectionStatus(StrEnum):
    SETUP_REQUIRED = "setup-required"
    READY = "ready"
    REAUTHENTICATION = "reauthentication-required"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class Connection(Contract):
    id: Identifier
    organisation_id: Identifier
    platform: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    roles: frozenset[ConnectionRole] = Field(min_length=1)
    interface: ConnectionInterface
    authorization: ConnectionAuthorization
    authorization_reference: str | None = Field(default=None, max_length=1024)
    capabilities: frozenset[str] = Field(min_length=1)
    allowed_resources: tuple[str, ...] = Field(min_length=1)
    http: HttpProviderApi | None = None
    status: ConnectionStatus
    authenticated_at: AwareDatetime | None = None
    authorization_expires_at: AwareDatetime | None = None
    last_validated_at: AwareDatetime | None = None
    region: str = Field(min_length=3, max_length=32)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_connection(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "kind" not in value:
            return value
        migrated = dict(value)
        kind = migrated.pop("kind")
        platform = migrated.pop("provider", None)
        reference = migrated.pop("auth_reference", None)
        role = "provider" if kind == "browser" else kind
        migrated.setdefault("platform", platform)
        migrated.setdefault("roles", [role])
        migrated.setdefault("interface", "browser" if kind == "browser" else "api")
        if kind == "browser":
            authorization = "browser-session"
        elif isinstance(reference, str) and reference.startswith("workload-identity://"):
            authorization = "workload-identity"
        elif isinstance(reference, str) and reference.startswith("oauth://"):
            authorization = "oauth"
        else:
            authorization = "api-key"
        migrated.setdefault("authorization", authorization)
        migrated.setdefault("authorization_reference", reference)
        return migrated

    @model_validator(mode="after")
    def validate_authentication(self) -> "Connection":
        if self.status is ConnectionStatus.READY and not self.authorization_reference:
            raise ValueError("a ready connection requires an authorization reference")
        browser = self.interface is ConnectionInterface.BROWSER
        browser_session = self.authorization is ConnectionAuthorization.BROWSER_SESSION
        if browser != browser_session:
            raise ValueError("browser connections require browser-session authorization")
        if browser and self.roles != frozenset({ConnectionRole.PROVIDER}):
            raise ValueError("browser connections provide only the provider role")
        if self.http is not None and (
            self.interface is not ConnectionInterface.API
            or ConnectionRole.PROVIDER not in self.roles
        ):
            raise ValueError("HTTP provider configuration requires an API provider role")
        if (
            self.interface is ConnectionInterface.API
            and ConnectionRole.PROVIDER in self.roles
            and self.http is None
        ):
            raise ValueError("API provider connections require an HTTP API declaration")
        if (
            self.authorization_expires_at is not None
            and self.authenticated_at is not None
            and self.authorization_expires_at <= self.authenticated_at
        ):
            raise ValueError("authorization expiry must follow authentication")
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
