from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PublisherSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIREKEY_",
        case_sensitive=False,
        extra="ignore",
    )

    project_id: str = ""
    firestore_database: str = "(default)"
    region: str = ""
    event_topic: str = "firekey-events"
    publish_timeout_seconds: int = Field(default=20, ge=1, le=120)
    outbox_lease_seconds: int = Field(default=60, ge=10, le=600)
    publish_batch_size: int = Field(default=20, ge=1, le=100)
    publish_max_events: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_delivery_window(self) -> "PublisherSettings":
        if len(self.project_id) < 4 or len(self.region) < 3:
            raise ValueError("project_id and region are required")
        if self.outbox_lease_seconds <= self.publish_timeout_seconds:
            raise ValueError("outbox lease must exceed the provider publish timeout")
        if self.publish_max_events < self.publish_batch_size:
            raise ValueError("publish max events must cover at least one full batch")
        return self
