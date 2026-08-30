from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class GoogleCloudOnboardingStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"


class GoogleCloudService(Contract):
    reference: str = Field(
        pattern=r"^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/locations/[a-z0-9-]+/services/[a-z][a-z0-9-]{0,61}[a-z0-9]$"
    )
    display_name: str = Field(min_length=1, max_length=63)
    region: str = Field(min_length=3, max_length=32)
    runtime_identity: str | None = Field(default=None, max_length=320)


class GoogleCloudServiceAccount(Contract):
    email: str = Field(
        max_length=320,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._%+-]{0,254}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.gserviceaccount\.com$",
    )
    display_name: str = Field(min_length=1, max_length=256)


class GoogleCloudProject(Contract):
    project_id: str = Field(
        min_length=6,
        max_length=30,
        pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$",
    )
    project_number: str = Field(pattern=r"^[1-9][0-9]{5,29}$")
    display_name: str = Field(min_length=1, max_length=256)
    services: tuple[GoogleCloudService, ...] = ()
    service_accounts: tuple[GoogleCloudServiceAccount, ...] = ()


class GoogleCloudOnboardingSession(Contract):
    id: Identifier
    organisation_id: Identifier
    subject: str = Field(min_length=1, max_length=512)
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: GoogleCloudOnboardingStatus
    projects: tuple[GoogleCloudProject, ...] = ()
    connection_id: Identifier | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    authorization_ciphertext: str | None = Field(default=None, max_length=16384)
    authorization_expires_at: AwareDatetime | None = None
    authorized_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_completion(self) -> "GoogleCloudOnboardingSession":
        complete = self.status is GoogleCloudOnboardingStatus.COMPLETE
        if complete != (self.completed_at is not None):
            raise ValueError("completed Google Cloud onboarding requires a completion time")
        if complete != bool(self.projects):
            raise ValueError("completed Google Cloud onboarding requires discovered projects")
        if self.connection_id is not None and not complete:
            raise ValueError("Google Cloud connection requires completed discovery")
        authorization = self.authorization_ciphertext is not None
        if authorization != (self.authorization_expires_at is not None):
            raise ValueError("Google Cloud authorization requires ciphertext and an expiry")
        if authorization and not complete:
            raise ValueError("Google Cloud authorization requires completed discovery")
        if self.authorized_at is not None and not complete:
            raise ValueError("Google Cloud authorization requires completed discovery")
        if self.authorized_at is not None and authorization:
            raise ValueError("completed Google Cloud authorization cannot retain access")
        return self
