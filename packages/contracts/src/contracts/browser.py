from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.playbook import Selector


class BrowserStatus(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    TAKEOVER = "human-takeover"
    CAPTURING = "secure-capture"
    COMPLETE = "complete"
    FAILED = "failed"
    TERMINATED = "terminated"


class BrowserAccessMode(StrEnum):
    VIEW = "view"
    TAKEOVER = "takeover"


class BrowserActionKind(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    KEY = "key"
    WAIT = "wait"


class BrowserActionStatus(StrEnum):
    AUTHORIZED = "authorized"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BrowserAction(Contract):
    id: Identifier
    session_id: Identifier
    kind: BrowserActionKind
    selector: Selector | None = None
    value: str | None = Field(default=None, max_length=4096)
    url: str | None = Field(default=None, max_length=2048)
    protected: bool = False
    expected_url: str | None = Field(default=None, max_length=2048)
    expected_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    fencing_token: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_target(self) -> "BrowserAction":
        if self.kind is BrowserActionKind.NAVIGATE and not self.url:
            raise ValueError("navigation requires a URL")
        if (
            self.kind in {BrowserActionKind.CLICK, BrowserActionKind.TYPE, BrowserActionKind.SELECT}
            and not self.selector
        ):
            raise ValueError("interactive actions require a selector")
        if self.kind in {BrowserActionKind.TYPE, BrowserActionKind.SELECT} and self.value is None:
            raise ValueError("input actions require a value")
        return self


class BrowserActionRecord(Contract):
    id: Identifier
    organisation_id: Identifier
    session_id: Identifier
    action: BrowserAction
    status: BrowserActionStatus
    error: str | None = Field(default=None, max_length=1024)
    authorised_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "BrowserActionRecord":
        terminal = self.status is not BrowserActionStatus.AUTHORIZED
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal browser actions require a completion time")
        if self.status is BrowserActionStatus.FAILED and not self.error:
            raise ValueError("failed browser actions require an error")
        return self


class BrowserPolicy(Contract):
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    allowed_actions: frozenset[BrowserActionKind] = Field(min_length=1)
    protected_operations: frozenset[str] = frozenset()
    max_steps: int = Field(default=40, ge=1, le=200)
    allow_downloads: bool = False
    allow_uploads: bool = False
    allow_clipboard: bool = False
    login_url_pattern: str | None = Field(default=None, max_length=1024)


class BrowserSession(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    playbook_id: Identifier
    playbook_version: Identifier
    provider_connection_id: Identifier
    worker_instance: str | None = Field(default=None, max_length=512)
    internal_address: str | None = Field(default=None, max_length=128)
    status: BrowserStatus
    policy: BrowserPolicy
    fencing_token: int = Field(gt=0)
    step_count: int = Field(default=0, ge=0)
    model_paused: bool = True
    recording_paused: bool = True
    takeover_subject: str | None = Field(default=None, max_length=512)
    replay_reference: str | None = Field(default=None, max_length=1024)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    updated_at: AwareDatetime
    terminated_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "BrowserSession":
        if self.step_count > self.policy.max_steps:
            raise ValueError("browser session exceeded its step budget")
        if self.status is BrowserStatus.CAPTURING and (
            not self.model_paused or not self.recording_paused
        ):
            raise ValueError("secure capture requires model and recording barriers")
        if self.status is BrowserStatus.TERMINATED and self.terminated_at is None:
            raise ValueError("terminated browser sessions require a termination time")
        return self


class ReplayCheckpoint(Contract):
    id: Identifier
    organisation_id: Identifier
    session_id: Identifier
    sequence: int = Field(ge=0)
    url: str = Field(min_length=1, max_length=2048)
    action: str = Field(min_length=1, max_length=128)
    image_reference: str = Field(min_length=1, max_length=1024)
    image_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    safety: tuple[str, ...] = ()
    human_takeover: bool = False
    recorded_at: AwareDatetime


class SecureCaptureResult(Contract):
    id: Identifier
    organisation_id: Identifier
    session_id: Identifier
    field_name: Identifier
    provider_id: str = Field(min_length=1, max_length=256)
    secret_reference: str = Field(min_length=1, max_length=1024)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    masked_value_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: AwareDatetime


class BrowserAccessGrant(Contract):
    organisation_id: Identifier
    session_id: Identifier
    mode: BrowserAccessMode
    gateway_url: str = Field(min_length=12, max_length=2048)
    capability: str = Field(min_length=32)
    expires_at: AwareDatetime
    session: BrowserSession


class SetupStatus(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    CAPTURING = "capturing"
    COMPLETE = "complete"
    TERMINATED = "terminated"


class SetupSession(Contract):
    id: Identifier
    organisation_id: Identifier
    connection_id: Identifier
    secret_container: str = Field(
        pattern=r"^projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+$", max_length=1024
    )
    token_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    subject: str = Field(min_length=1, max_length=512)
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    worker_instance: str | None = Field(default=None, max_length=512)
    internal_address: str | None = Field(default=None, max_length=128)
    status: SetupStatus
    auth_reference: str | None = Field(default=None, max_length=1024)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    updated_at: AwareDatetime
    terminated_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "SetupSession":
        completed = self.status is SetupStatus.COMPLETE
        if completed != (self.auth_reference is not None):
            raise ValueError("a completed setup session requires its authentication reference")
        if self.status is SetupStatus.TERMINATED and self.terminated_at is None:
            raise ValueError("terminated setup sessions require a termination time")
        if self.status in {SetupStatus.READY, SetupStatus.CAPTURING} and (
            self.worker_instance is None or self.internal_address is None
        ):
            raise ValueError("an active setup session requires its worker binding")
        return self


class ConnectionWaiter(Contract):
    organisation_id: Identifier
    connection_id: Identifier
    run_ids: tuple[Identifier, ...] = ()
    revision: int = Field(default=0, ge=0)
