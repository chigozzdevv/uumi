import re
from urllib.parse import urlsplit

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
    model_armor_template: str = ""
    capability_public_key: str = ""
    evidence_bucket: str = ""
    github_app_slug: str = Field(default="", max_length=100)
    github_client_id: str = Field(default="", max_length=256)
    github_client_secret: str = Field(default="", max_length=1024)
    github_callback_url: str = Field(default="", max_length=2048)

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
        if not _secret_version(self.capability_secret, self.project_id):
            raise ValueError(
                "capability secret must be an immutable project Secret Manager version"
            )
        github = (
            self.github_app_slug,
            self.github_client_id,
            self.github_client_secret,
            self.github_callback_url,
        )
        if any(github) and not all(github):
            raise ValueError("GitHub App onboarding configuration is incomplete")
        if self.github_app_slug and not re.fullmatch(r"[A-Za-z0-9-]+", self.github_app_slug):
            raise ValueError("GitHub App slug is invalid")
        if self.github_client_id and not re.fullmatch(r"[A-Za-z0-9._-]+", self.github_client_id):
            raise ValueError("GitHub client ID is invalid")
        if self.github_client_secret and not _secret_version(
            self.github_client_secret, self.project_id
        ):
            raise ValueError(
                "GitHub client secret must be an immutable project Secret Manager version"
            )
        if self.github_callback_url:
            callback = urlsplit(self.github_callback_url)
            if (
                callback.scheme != "https"
                or callback.hostname is None
                or callback.username is not None
                or callback.password is not None
                or callback.fragment
            ):
                raise ValueError("GitHub callback URL must be an HTTPS URL without credentials")
        if self.model_armor_template and not self.model_armor_template.startswith("projects/"):
            raise ValueError("Model Armor template must be a full resource name")
        return self


def _secret_version(value: str, project_id: str) -> bool:
    return (
        re.fullmatch(
            rf"projects/{re.escape(project_id)}/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*",
            value,
        )
        is not None
    )
