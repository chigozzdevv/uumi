from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", case_sensitive=False, extra="ignore")

    project_id: str = Field(default="", min_length=4)
    firestore_database: str = "(default)"
    region: str = Field(default="", min_length=3)
    evidence_bucket: str = Field(default="", min_length=3)
    capability_secret: str = Field(default="", min_length=20)

    @model_validator(mode="after")
    def require_runtime_configuration(self) -> "BrokerSettings":
        if not all((self.project_id, self.region, self.evidence_bucket, self.capability_secret)):
            raise ValueError(
                "broker project, region, evidence bucket, and capability secret required"
            )
        if not self.capability_secret.startswith("projects/"):
            raise ValueError("capability secret must be a full Secret Manager version")
        return self
