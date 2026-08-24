from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UUMI_",
        case_sensitive=False,
        extra="ignore",
    )

    project_id: str = Field(min_length=4)
    api_url: str = Field(min_length=12, max_length=2048)
    maximum_body_bytes: int = Field(default=10_485_760, ge=1024, le=10_485_760)
    upstream_timeout_seconds: float = Field(default=50.0, gt=0, le=55)

    @model_validator(mode="after")
    def validate_api_url(self) -> "WebSettings":
        parsed = urlsplit(self.api_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.path not in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API URL must be an HTTPS origin without credentials")
        return self
