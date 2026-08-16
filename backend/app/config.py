from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    google_cloud_project: str = "tripagent-505715"
    google_cloud_location: str = "global"
    google_genai_use_vertexai: bool = True
    gemini_model_id: str = ""
    pubsub_topic_id: str = "trip-disruptions"
    pubsub_transport: Literal["google", "local"] = "google"
    process_events_inline: bool = False
    seed_demo_data: bool = False
    event_lease_seconds: int = Field(default=60, ge=5, le=600)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_local_mode(self) -> "Settings":
        if self.process_events_inline and self.pubsub_transport != "local":
            raise ValueError("PROCESS_EVENTS_INLINE requires PUBSUB_TRANSPORT=local")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
