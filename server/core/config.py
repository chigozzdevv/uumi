import re
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UUMI_",
        case_sensitive=False,
        extra="ignore",
    )

    project_id: str = Field(default="", min_length=4)
    firestore_database: str = "(default)"
    region: str = Field(default="", min_length=3, max_length=32)
    oidc_audience: str = Field(default="", min_length=8)
    capability_secret: str = Field(default="", min_length=20)
    browser_gateway_url: str = Field(default="", min_length=12)
    browser_setup_url: str = Field(default="https://uumi.web.app/browser/setup", min_length=12)
    walkthrough_bucket: str = Field(default="", min_length=3)
    browser_zone: str = ""
    browser_template: str = ""
    browser_worker_image: str = ""
    model_armor_template: str = ""
    model_armor_response_template: str = ""
    capability_public_key: str = ""
    evidence_bucket: str = ""
    github_app_slug: str = Field(default="", max_length=100)
    github_client_id: str = Field(default="", max_length=256)
    github_client_secret: str = Field(default="", max_length=1024)
    github_callback_url: str = Field(default="", max_length=2048)
    google_cloud_client_id: str = Field(default="", max_length=512)
    google_cloud_client_secret: str = Field(default="", max_length=1024)
    google_cloud_callback_url: str = Field(default="", max_length=2048)
    google_cloud_onboarding_kms_key: str = Field(default="", max_length=1024)
    google_cloud_discovery_service_account: str = Field(default="", max_length=320)
    broker_url: str = Field(default="", max_length=2048)
    broker_service_account: str = Field(default="", max_length=320)
    notification_email_secret_version: str = Field(default="", max_length=1024)
    notification_email_sender: str = Field(default="", max_length=320)

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
        google_cloud = (
            self.google_cloud_client_id,
            self.google_cloud_client_secret,
            self.google_cloud_callback_url,
            self.google_cloud_onboarding_kms_key,
        )
        if any(google_cloud) and not all(google_cloud):
            raise ValueError("Google Cloud onboarding configuration is incomplete")
        if all(google_cloud) and not all(
            (
                self.broker_url,
                self.broker_service_account,
                self.google_cloud_discovery_service_account,
            )
        ):
            raise ValueError("Google Cloud broker configuration is incomplete")
        if self.google_cloud_client_secret and not _secret_version(
            self.google_cloud_client_secret, self.project_id
        ):
            raise ValueError(
                "Google Cloud client secret must be an immutable project Secret Manager version"
            )
        if self.google_cloud_callback_url:
            callback = urlsplit(self.google_cloud_callback_url)
            if (
                callback.scheme != "https"
                or callback.hostname is None
                or callback.username is not None
                or callback.password is not None
                or callback.fragment
            ):
                raise ValueError(
                    "Google Cloud callback URL must be an HTTPS URL without credentials"
                )
        if self.google_cloud_onboarding_kms_key and not re.fullmatch(
            rf"projects/{re.escape(self.project_id)}/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+",
            self.google_cloud_onboarding_kms_key,
        ):
            raise ValueError("Google Cloud onboarding KMS key is invalid")
        if self.broker_url:
            broker = urlsplit(self.broker_url)
            if (
                broker.scheme != "https"
                or broker.hostname is None
                or broker.username is not None
                or broker.password is not None
                or broker.query
                or broker.fragment
            ):
                raise ValueError("broker URL must be an HTTPS origin without credentials")
        if self.broker_service_account and not _service_account(self.broker_service_account):
            raise ValueError("broker service account is invalid")
        if self.google_cloud_discovery_service_account and not _service_account(
            self.google_cloud_discovery_service_account
        ):
            raise ValueError("Google Cloud discovery service account is invalid")
        email_delivery = (
            self.notification_email_secret_version,
            self.notification_email_sender,
        )
        if any(email_delivery) and not all(email_delivery):
            raise ValueError("email notification delivery configuration is incomplete")
        if self.notification_email_secret_version and not _secret_version(
            self.notification_email_secret_version, self.project_id
        ):
            raise ValueError(
                "email notification secret must be an immutable project Secret Manager version"
            )
        if self.notification_email_sender and not _email(self.notification_email_sender):
            raise ValueError("email notification sender is invalid")
        if self.model_armor_template and not self.model_armor_template.startswith("projects/"):
            raise ValueError("Model Armor template must be a full resource name")
        if self.model_armor_response_template and not self.model_armor_response_template.startswith(
            "projects/"
        ):
            raise ValueError("Model Armor response template must be a full resource name")
        return self


def _secret_version(value: str, project_id: str) -> bool:
    return (
        re.fullmatch(
            rf"projects/{re.escape(project_id)}/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*",
            value,
        )
        is not None
    )


def _email(value: str) -> bool:
    local, separator, domain = value.strip().lower().rpartition("@")
    return bool(separator and local and "." in domain and not domain.startswith("."))


def _service_account(value: str) -> bool:
    return (
        re.fullmatch(
            r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com",
            value,
        )
        is not None
    )
