from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class GitHubOnboardingStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"


class GitHubSecretScanningStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class GitHubOnboardingSession(Contract):
    id: Identifier
    organisation_id: Identifier
    subject: str = Field(min_length=1, max_length=512)
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: GitHubOnboardingStatus
    installation_id: int | None = Field(default=None, gt=0)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_completion(self) -> "GitHubOnboardingSession":
        complete = self.status is GitHubOnboardingStatus.COMPLETE
        if complete != (self.installation_id is not None and self.completed_at is not None):
            raise ValueError("completed GitHub onboarding requires installation metadata")
        return self


class GitHubInstallation(Contract):
    installation_id: int = Field(gt=0)
    organisation_id: Identifier
    account_id: int = Field(gt=0)
    account_login: str = Field(min_length=1, max_length=256)
    account_type: str = Field(min_length=1, max_length=64)
    repository_selection: str = Field(pattern=r"^(all|selected)$")
    permissions: dict[str, str] = Field(default_factory=dict, max_length=100)
    events: tuple[str, ...] = ()
    webhook_verified_at: AwareDatetime | None = None
    repositories_ready: bool = False
    active: bool = True
    deleted: bool = False
    ready: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_app_contract(self) -> "GitHubInstallation":
        if any(len(key) > 96 or len(value) > 32 for key, value in self.permissions.items()):
            raise ValueError("GitHub installation permissions are invalid")
        if any(len(event) > 96 for event in self.events):
            raise ValueError("GitHub installation events are invalid")
        if self.ready and (
            self.webhook_verified_at is None or not self.repositories_ready or not self.active
        ):
            raise ValueError("a ready GitHub installation requires webhook and repository checks")
        if self.deleted and (self.active or self.ready):
            raise ValueError("a deleted GitHub installation cannot be active or ready")
        return self


class GitHubInstallationIndex(Contract):
    installation_id: int = Field(gt=0)
    organisation_id: Identifier
    onboarding_id: Identifier
    active: bool = True
    deleted: bool = False
    ready: bool = False
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "GitHubInstallationIndex":
        if self.ready and not self.active:
            raise ValueError("a ready GitHub installation index must be active")
        if self.deleted and (self.active or self.ready):
            raise ValueError("a deleted GitHub installation index cannot be active or ready")
        return self


class GitHubRepository(Contract):
    repository_id: int = Field(gt=0)
    installation_id: int = Field(gt=0)
    organisation_id: Identifier
    full_name: str = Field(min_length=3, max_length=256, pattern=r"^[^/]+/[^/]+$")
    private: bool
    default_branch: str = Field(min_length=1, max_length=256)
    secret_scanning: GitHubSecretScanningStatus
    credential_id: Identifier
    updated_at: AwareDatetime


class GitHubWebhookReceipt(Contract):
    installation_id: int = Field(gt=0)
    delivery_id: str = Field(min_length=1, max_length=256)
    event: str = Field(min_length=1, max_length=96)
    action: str = Field(min_length=1, max_length=96)
    received_at: AwareDatetime
