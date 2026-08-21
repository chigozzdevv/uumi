from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.http import HttpAuthScheme, HttpProviderApi
from contracts.verification import DownstreamConfirmation, ProbeKind


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
    playbook_id: Identifier | None = None
    playbook_version_id: Identifier | None = None
    status: ConnectionStatus
    authenticated_at: AwareDatetime | None = None
    authorization_expires_at: AwareDatetime | None = None
    last_validated_at: AwareDatetime | None = None
    region: str = Field(min_length=3, max_length=32)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    archived_at: AwareDatetime | None = None
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
        playbook_attached = self.playbook_id is not None and self.playbook_version_id is not None
        if (self.playbook_id is None) != (self.playbook_version_id is None):
            raise ValueError("connection playbook identity and version must be set together")
        if browser != browser_session:
            raise ValueError("browser connections require browser-session authorization")
        if browser and self.roles != frozenset({ConnectionRole.PROVIDER}):
            raise ValueError("browser connections provide only the provider role")
        if not browser and playbook_attached:
            raise ValueError("only browser connections can attach a playbook")
        if browser and self.status is ConnectionStatus.READY and not playbook_attached:
            raise ValueError("a ready browser connection requires a published playbook")
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
            self.interface is ConnectionInterface.API
            and ConnectionRole.PROVIDER in self.roles
            and self.authorization
            not in {ConnectionAuthorization.OAUTH, ConnectionAuthorization.API_KEY}
        ):
            raise ValueError("HTTP provider connections require OAuth or API-key authorization")
        if (
            self.authorization is ConnectionAuthorization.OAUTH
            and self.http is not None
            and self.http.auth.scheme is not HttpAuthScheme.BEARER
        ):
            raise ValueError("OAuth provider connections require bearer request authentication")
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
    archived_at: AwareDatetime | None = None
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
    archived_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)


class FunctionalVerification(Contract):
    kind: ProbeKind
    target: str = Field(min_length=12, max_length=1024)
    method: str = Field(default="POST", pattern=r"^(GET|POST)$")
    expected_status: tuple[int, ...] = Field(default=(200,), min_length=1)
    required_fields: dict[str, str | int | bool] = Field(default_factory=dict)
    confirmation: DownstreamConfirmation | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def validate_functional_verification(self) -> "FunctionalVerification":
        if self.kind not in {ProbeKind.HTTP, ProbeKind.EMAIL}:
            raise ValueError("service verification must use an HTTP or email probe")
        if self.kind is ProbeKind.HTTP and not self.required_fields:
            raise ValueError("HTTP service verification requires an expected result field")
        if self.kind is ProbeKind.EMAIL and self.confirmation is None:
            raise ValueError("email service verification requires downstream confirmation")
        if self.kind is ProbeKind.HTTP and self.confirmation is not None:
            raise ValueError("downstream confirmation is only valid for email verification")
        if any(not 100 <= status <= 599 for status in self.expected_status):
            raise ValueError("service verification statuses must be valid HTTP status codes")
        return self


class ConsumerService(Contract):
    id: Identifier
    organisation_id: Identifier
    application_id: Identifier
    environment_id: Identifier
    runtime_connection_id: Identifier
    telemetry_connection_ids: tuple[Identifier, ...] = ()
    runtime_resource: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=160)
    verification: FunctionalVerification | None = None
    repository: str | None = Field(default=None, max_length=256)
    identity: str | None = Field(default=None, min_length=1, max_length=512)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    archived_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_connections(self) -> "ConsumerService":
        if len(set(self.telemetry_connection_ids)) != len(self.telemetry_connection_ids):
            raise ValueError("service telemetry connection IDs must be unique")
        if self.runtime_connection_id in self.telemetry_connection_ids:
            raise ValueError("service runtime and telemetry connections must be distinct")
        return self
