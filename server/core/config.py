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
    region: str = Field(default="", min_length=3, max_length=32)
    oidc_audience: str = Field(default="", min_length=8)
    capability_secret: str = Field(default="", min_length=20)
    browser_gateway_url: str = Field(default="", min_length=12)
    walkthrough_bucket: str = Field(default="", min_length=3)
    browser_zone: str = ""
    browser_template: str = ""
    browser_worker_image: str = ""
    capability_public_key: str = ""
    evidence_bucket: str = ""

    @model_validator(mode="after")
    def require_runtime_configuration(self) -> "Settings":
        if not all(
            (
                self.project_id,
                self.region,
                self.oidc_audience,
                self.capability_secret,
                self.browser_gateway_url,
                self.walkthrough_bucket,
            )
        ):
            raise ValueError("API runtime configuration is incomplete")
        if not self.capability_secret.startswith("projects/"):
            raise ValueError("capability secret must be a full Secret Manager version")
        return self
