from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.plan import OperationStep
from contracts.state import Stage


class PlaybookState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
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
    provider_id_selector: Selector


class StepOutput(Contract):
    name: Identifier
    selector: Selector
    attribute: str = Field(default="text", pattern=r"^(text|value)$")


class PlaybookStep(OperationStep):
    selectors: tuple[Selector, ...] = ()
    checkpoint: PageCheckpoint | None = None
    secure_field: SecureField | None = None
    outputs: tuple[StepOutput, ...] = ()
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    retry_limit: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def validate_capture(self) -> "PlaybookStep":
        if self.secure_field is not None and self.tool != "browser.secure-capture":
            raise ValueError("secure fields can only be handled by browser.secure-capture")
        if self.tool.startswith("browser.") and self.operation != "navigate" and not self.selectors:
            raise ValueError("browser actions require deterministic selectors")
        if self.tool.startswith("browser.") and len(self.selectors) > 1:
            raise ValueError("each browser step must target exactly one control")
        if self.tool.startswith("browser.") and self.checkpoint is None:
            raise ValueError("browser actions require a deterministic page checkpoint")
        return self


class PlaybookDraft(Contract):
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=64)
    allowed_domains: tuple[str, ...] = ()
    steps: tuple[PlaybookStep, ...] = Field(min_length=1)
    login_url_pattern: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="before")
    @classmethod
    def migrate_provider(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "platform" in value or "provider" not in value:
            return value
        migrated = dict(value)
        migrated["platform"] = migrated.pop("provider")
        migrated.pop("execution", None)
        migrated.pop("allowed_tools", None)
        migrated.pop("required_connections", None)
        migrated.pop("recovery", None)
        return migrated

    @model_validator(mode="after")
    def validate_browser_procedure(self) -> "PlaybookDraft":
        if not self.allowed_domains or not self.login_url_pattern:
            raise ValueError("browser playbooks require domains and a login URL pattern")
        if any(not step.tool.startswith("browser.") for step in self.steps):
            raise ValueError("playbooks contain browser steps only")
        if any(step.protected for step in self.steps):
            raise ValueError("playbooks cannot declare approvals; policy protects operations")
        if not any(step.stage is Stage.CREATE and step.secure_field for step in self.steps):
            raise ValueError("browser playbooks require a secure capture step in create")
        if not any(step.stage is Stage.REVOKE for step in self.steps):
            raise ValueError("browser playbooks require revoke steps")
        if tuple(dict.fromkeys(step.id for step in self.steps)) != tuple(
            step.id for step in self.steps
        ):
            raise ValueError("playbook step IDs must be unique")
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
    published_by: Identifier | None = None
    published_at: AwareDatetime | None = None
    created_by: Identifier
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_publication(self) -> "PlaybookVersion":
        published = self.published_by is not None and self.published_at is not None
        if (self.published_by is None) != (self.published_at is None):
            raise ValueError("playbook publisher identity and time must be set together")
        if self.state is PlaybookState.PUBLISHED and not published:
            raise ValueError("published playbooks require publisher identity and time")
        return self


class Playbook(Contract):
    id: Identifier
    organisation_id: Identifier
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=64)
    latest_version: int = Field(default=0, ge=0)
    active_version_id: Identifier | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)
