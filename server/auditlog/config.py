from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuditLogSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UUMI_", case_sensitive=False, extra="ignore")

    project_id: str = ""
    firestore_database: str = "(default)"
    region: str = ""
    oidc_audience: str = ""
    trusted_push_service_account: str = ""
    lease_seconds: int = Field(default=60, ge=15, le=600)
    batch_size: int = Field(default=20, ge=1, le=100)
    maximum_events: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_runtime(self) -> "AuditLogSettings":
        if not all(
            (
                self.project_id,
                self.region,
                self.oidc_audience,
                self.trusted_push_service_account,
            )
        ):
            raise ValueError("audit log runtime configuration is incomplete")
        if self.maximum_events < self.batch_size:
            raise ValueError("audit maximum events must cover a full batch")
        return self
