from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", extra="ignore")

    project_id: str = Field(min_length=4)
    firestore_database: str = "(default)"
    oidc_audience: str = Field(min_length=8)
    scc_push_service_account: str = Field(pattern=r"^[^@]+@[^@]+\.iam\.gserviceaccount\.com$")
    github_secret_project: str = Field(min_length=4)
    trusted_push_service_accounts: frozenset[str] = Field(min_length=1)
    provider_secret_prefix: str = "firekey-provider-webhook"
    max_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    webhook_replay_seconds: int = Field(default=300, ge=30, le=900)

    @model_validator(mode="after")
    def validate_projects(self) -> "IngestionSettings":
        if self.github_secret_project != self.project_id:
            raise ValueError("GitHub webhook secrets must remain in the FireKey project")
        if self.scc_push_service_account not in self.trusted_push_service_accounts:
            raise ValueError("SCC push identity must be included in trusted push identities")
        return self
