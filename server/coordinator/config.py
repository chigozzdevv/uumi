from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoordinatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UUMI_", case_sensitive=False, extra="ignore")

    project_id: str = Field(default="", min_length=4)
    firestore_database: str = "(default)"
    region: str = Field(default="", min_length=3)
    zone: str = Field(default="", min_length=3)
    evidence_bucket: str = Field(default="", min_length=3)
    broker_url: str = Field(default="", min_length=12)
    browser_template: str = Field(default="", min_length=12)
    capability_secret: str = Field(default="", min_length=20)
    oidc_audience: str = Field(default="", min_length=8)
    browser_image: str = Field(default="", min_length=20)
    model_armor_template: str = Field(default="", min_length=20)

    @model_validator(mode="after")
    def validate_runtime(self) -> "CoordinatorSettings":
        required = (
            self.project_id,
            self.region,
            self.zone,
            self.evidence_bucket,
            self.broker_url,
            self.browser_template,
            self.capability_secret,
            self.oidc_audience,
            self.browser_image,
            self.model_armor_template,
        )
        if not all(required):
            raise ValueError("coordinator runtime configuration is incomplete")
        if not self.capability_secret.startswith("projects/"):
            raise ValueError("capability secret must be a full Secret Manager version")
        if not self.browser_template.startswith("projects/"):
            raise ValueError("browser template must be a full Compute Engine resource")
        if not self.model_armor_template.startswith("projects/"):
            raise ValueError("Model Armor template must be a full resource name")
        return self
