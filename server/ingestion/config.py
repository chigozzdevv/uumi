from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", extra="ignore")

    project_id: str = Field(min_length=4)
    firestore_database: str = "(default)"
    oidc_audience: str = Field(min_length=8)
    scc_push_service_account: str = Field(pattern=r"^[^@]+@[^@]+\.iam\.gserviceaccount\.com$")
    github_secret_project: str = Field(min_length=4)
    max_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_projects(self) -> "IngestionSettings":
        if self.github_secret_project != self.project_id:
            raise ValueError("GitHub webhook secrets must remain in the FireKey project")
        return self
