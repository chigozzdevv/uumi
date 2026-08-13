from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.state import Stage


class ExecutionMethod(StrEnum):
    API = "provider-api"
    CLI = "cli"
    COMPUTER = "computer-use"
    HUMAN = "human-assisted"


class PlaybookState(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    TEST = "test-required"
    APPROVAL = "approval-required"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVIEW = "review-required"


class SelectorKind(StrEnum):
    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    TEST_ID = "test-id"
    CSS = "css"


class Selector(Contract):
    kind: SelectorKind
    value: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=256)
    exact: bool = True


class PageCheckpoint(Contract):
    url_pattern: str = Field(min_length=1, max_length=1024)
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()


class SecureField(Contract):
    name: Identifier
    selector: Selector
    sink_connection_id: Identifier
    secret_resource: str = Field(min_length=1, max_length=1024)


class PlaybookStep(Contract):
    id: Identifier
    stage: Stage
    tool: str = Field(min_length=3, max_length=128)
    operation: str = Field(min_length=1, max_length=96)
    parameters: dict[str, str | int | bool | tuple[str, ...]] = Field(default_factory=dict)
    selectors: tuple[Selector, ...] = ()
    checkpoint: PageCheckpoint | None = None
    protected: bool = False
    secure_field: SecureField | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    retry_limit: int = Field(default=0, ge=0, le=5)
    evidence_checks: frozenset[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_capture(self) -> "PlaybookStep":
        if self.secure_field is not None and self.tool != "browser.secure-capture":
            raise ValueError("secure fields can only be handled by browser.secure-capture")
        if self.tool.startswith("browser.") and self.operation != "navigate" and not self.selectors:
            raise ValueError("browser actions require deterministic selectors")
        return self


class PlaybookDraft(Contract):
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=64)
    execution: ExecutionMethod
    allowed_domains: tuple[str, ...] = ()
    allowed_tools: frozenset[str] = Field(min_length=1)
    required_connections: tuple[Identifier, ...] = Field(min_length=1)
    steps: tuple[PlaybookStep, ...] = Field(min_length=1)
    recovery: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution(self) -> "PlaybookDraft":
        browser_steps = tuple(step for step in self.steps if step.tool.startswith("browser."))
        if self.execution is ExecutionMethod.COMPUTER:
            if not self.allowed_domains or not browser_steps:
                raise ValueError("computer-use playbooks require domains and browser steps")
            if not any(step.secure_field for step in browser_steps):
                raise ValueError("computer-use playbooks require an explicit secure capture step")
        elif browser_steps:
            raise ValueError("browser steps require computer-use execution")
        if tuple(dict.fromkeys(step.id for step in self.steps)) != tuple(
            step.id for step in self.steps
        ):
            raise ValueError("playbook step IDs must be unique")
        return self


class DryRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class DryRun(Contract):
    id: Identifier
    organisation_id: Identifier
    playbook_id: Identifier
    version_id: Identifier
    status: DryRunStatus
    environment_id: Identifier
    checks: frozenset[str] = frozenset()
    evidence_ids: tuple[Identifier, ...] = ()
    replay_reference: str | None = Field(default=None, max_length=1024)
    failure: str | None = Field(default=None, max_length=1024)
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "DryRun":
        terminal = self.status in {DryRunStatus.PASSED, DryRunStatus.FAILED}
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal dry runs require a completion time")
        if self.status is DryRunStatus.PASSED and not self.evidence_ids:
            raise ValueError("a passed dry run requires evidence")
        if self.status is DryRunStatus.FAILED and not self.failure:
            raise ValueError("a failed dry run requires a reason")
        return self


class PlaybookVersion(Contract):
    id: Identifier
    organisation_id: Identifier
    playbook_id: Identifier
    number: int = Field(gt=0)
    definition: PlaybookDraft
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: PlaybookState
    source_ids: tuple[Identifier, ...] = ()
    dry_run_id: Identifier | None = None
    approved_by: Identifier | None = None
    approved_at: AwareDatetime | None = None
    created_by: Identifier
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_activation(self) -> "PlaybookVersion":
        approved = self.approved_by is not None and self.approved_at is not None
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError("playbook approval identity and time must be set together")
        if self.state is PlaybookState.ACTIVE and (not approved or self.dry_run_id is None):
            raise ValueError("active playbooks require approval and a passed dry run")
        return self


class Playbook(Contract):
    id: Identifier
    organisation_id: Identifier
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=64)
    latest_version: int = Field(default=0, ge=0)
    active_version_id: Identifier | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)


class PlaybookAssignment(Contract):
    id: Identifier
    organisation_id: Identifier
    credential_id: Identifier
    playbook_id: Identifier
    version_id: Identifier
    connection_ids: tuple[Identifier, ...] = Field(min_length=1)
    assigned_by: Identifier
    assigned_at: AwareDatetime
