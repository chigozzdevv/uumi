from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", case_sensitive=False, extra="ignore")

    project_id: str = Field(default="", min_length=4)
    firestore_database: str = "(default)"
    region: str = Field(default="", min_length=3)
    evidence_bucket: str = Field(default="", min_length=3)
    capability_public_key: str = Field(default="", min_length=40, max_length=64)
    attempt_lease_seconds: int = Field(default=120, ge=30, le=900)

    @model_validator(mode="after")
    def require_runtime_configuration(self) -> "BrokerSettings":
        if not all(
            (self.project_id, self.region, self.evidence_bucket, self.capability_public_key)
        ):
            raise ValueError(
                "broker project, region, evidence bucket, and capability public key required"
            )
        return self
