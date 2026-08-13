from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIREKEY_",
        case_sensitive=False,
        extra="ignore",
    )

    project_id: str = Field(default="", min_length=4)
    firestore_database: str = "(default)"
    oidc_audience: str = Field(default="", min_length=8)

    @model_validator(mode="after")
    def require_runtime_configuration(self) -> "Settings":
        if not self.project_id or not self.oidc_audience:
            raise ValueError("project_id and oidc_audience are required")
        return self
