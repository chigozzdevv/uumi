from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NotificationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIREKEY_",
        case_sensitive=False,
        extra="ignore",
    )

    project_id: str = ""
    firestore_database: str = "(default)"
    oidc_audience: str = ""
    trusted_push_service_account: str = ""
    app_url: str = ""
    lease_seconds: int = Field(default=60, ge=15, le=600)
    batch_size: int = Field(default=20, ge=1, le=100)
    maximum_deliveries: int = Field(default=100, ge=1, le=500)
    maximum_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)

    @model_validator(mode="after")
    def validate_runtime(self) -> "NotificationSettings":
        if not all(
            (
                self.project_id,
                self.oidc_audience,
                self.trusted_push_service_account,
                self.app_url,
            )
        ):
            raise ValueError("notification runtime configuration is incomplete")
        if self.maximum_deliveries < self.batch_size:
            raise ValueError("notification delivery maximum must cover a full batch")
        return self
