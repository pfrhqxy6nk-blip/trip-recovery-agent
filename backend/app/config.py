import os
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
    pubsub_command_topic_id: str = "trip-workflow-commands"
    pubsub_transport: Literal["google", "local"] = "google"
    process_events_inline: bool = False
    seed_demo_data: bool = False
    event_lease_seconds: int = Field(default=60, ge=5, le=600)
    log_level: str = "INFO"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    approval_callback_signing_key: str = ""
    enable_pilot_trip: bool = False
    enable_simulator: bool = False
    simulator_secret: str = ""
    enable_byok_connections: bool = False
    connection_base_url: str = ""
    byok_secret_resource_name: str = ""
    app_role: Literal["all", "worker", "edge"] = "worker"
    worker_base_url: str = ""
    enable_amadeus_flight_monitoring: bool = False
    amadeus_client_id: str = ""
    amadeus_client_secret: str = ""
    amadeus_base_url: str = "https://api.amadeus.com"
    amadeus_max_requests_per_hour: int = Field(default=100, ge=1, le=10_000)
    enable_trip_watch: bool = False
    trip_watch_max_checks_per_tick: int = Field(default=20, ge=1, le=100)
    # Calendar is deliberately opt-in: without a configured OAuth client the
    # worker must remain unable to request consent or mutate a user's calendar.
    enable_calendar_connections: bool = False
    enable_calendar_actions: bool = False
    calendar_client_id: str = ""
    calendar_client_secret_resource_name: str = ""
    calendar_oauth_signing_key: str = ""
    calendar_redirect_uri: str = ""
    calendar_id: str = "primary"
    # Refresh tokens from Calendar/Gmail are kept as immutable versions in a
    # dedicated Secret Manager secret.  They must never share a secret with
    # user-supplied Gemini keys or the OAuth client credential itself.
    oauth_refresh_tokens_secret_resource_name: str = ""
    # Gmail is a separate, draft-only connection. It requests gmail.compose only;
    # the adapter has no send endpoint and must never browse a user's inbox.
    enable_gmail_connections: bool = False
    enable_gmail_drafts: bool = False
    gmail_client_id: str = ""
    gmail_client_secret_resource_name: str = ""
    gmail_oauth_signing_key: str = ""
    gmail_redirect_uri: str = ""
    # Duffel is search-only until an explicit order-change adapter and payment
    # approval path are configured. It can provide a live, expiring quote safely.
    enable_duffel_quotes: bool = False
    duffel_access_token: str = ""
    duffel_access_token_secret_resource_name: str = ""
    enable_judge_mode: bool = False
    judge_daily_vertex_calls: int = Field(default=20, ge=1, le=200)
    judge_daily_vertex_calls_per_user: int = Field(default=5, ge=1, le=50)
    # A three-option Google Search plan includes a transport and stay for each
    # option.  256 tokens routinely truncates that JSON and makes a live search
    # look like an outage, so keep a bounded but usable ceiling.
    judge_max_output_tokens: int = Field(default=4096, ge=256, le=4096)

    @model_validator(mode="after")
    def validate_local_mode(self) -> "Settings":
        if self.process_events_inline and self.pubsub_transport != "local":
            raise ValueError("PROCESS_EVENTS_INLINE requires PUBSUB_TRANSPORT=local")
        # ``all`` mounts both the public edge and the private worker routes and
        # is intentionally available only for local integration tests.  A
        # Google Pub/Sub deployment must select one least-privilege role so a
        # misconfigured Cloud Run revision cannot accidentally expose worker
        # endpoints without the IAM boundary.
        if self.app_role == "all" and (self.pubsub_transport != "local" or os.getenv("K_SERVICE")):
            raise ValueError("APP_ROLE=all is local-only; use APP_ROLE=worker or APP_ROLE=edge")
        if self.telegram_bot_token and (
            len(self.telegram_webhook_secret.encode("utf-8")) < 16
            or len(self.approval_callback_signing_key.encode("utf-8")) < 32
        ):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN requires TELEGRAM_WEBHOOK_SECRET (16+ bytes) and "
                "APPROVAL_CALLBACK_SIGNING_KEY (32+ bytes)"
            )
        if self.enable_simulator and len(self.simulator_secret.encode("utf-8")) < 16:
            raise ValueError("ENABLE_SIMULATOR requires SIMULATOR_SECRET (16+ bytes)")
        if self.enable_byok_connections and not self.connection_base_url.startswith("https://"):
            raise ValueError("ENABLE_BYOK_CONNECTIONS requires an HTTPS CONNECTION_BASE_URL")
        if self.enable_byok_connections and not self.byok_secret_resource_name.startswith(
            f"projects/{self.google_cloud_project}/secrets/"
        ):
            raise ValueError(
                "ENABLE_BYOK_CONNECTIONS requires BYOK_SECRET_RESOURCE_NAME "
                "in the configured project"
            )
        if self.worker_base_url and not self.worker_base_url.startswith("https://"):
            raise ValueError("WORKER_BASE_URL must use HTTPS")
        if self.app_role == "edge" and not self.worker_base_url:
            raise ValueError("APP_ROLE=edge requires WORKER_BASE_URL")
        if self.app_role == "edge" and len(self.telegram_webhook_secret.encode("utf-8")) < 16:
            raise ValueError("APP_ROLE=edge requires TELEGRAM_WEBHOOK_SECRET (16+ bytes)")
        if self.enable_amadeus_flight_monitoring:
            if not self.amadeus_client_id or not self.amadeus_client_secret:
                raise ValueError(
                    "ENABLE_AMADEUS_FLIGHT_MONITORING requires AMADEUS_CLIENT_ID and "
                    "AMADEUS_CLIENT_SECRET"
                )
            if self.amadeus_base_url != "https://api.amadeus.com":
                raise ValueError("live flight monitoring requires the Amadeus production URL")
        if self.enable_trip_watch and not self.gemini_model_id:
            raise ValueError("ENABLE_TRIP_WATCH requires GEMINI_MODEL_ID")
        if self.enable_calendar_actions:
            self.enable_calendar_connections = True
        if self.enable_calendar_connections:
            if not self.calendar_client_id:
                raise ValueError("ENABLE_CALENDAR_CONNECTIONS requires CALENDAR_CLIENT_ID")
            if (
                not self.calendar_client_secret_resource_name.startswith(
                    f"projects/{self.google_cloud_project}/secrets/"
                )
                or "/versions/" not in self.calendar_client_secret_resource_name
            ):
                raise ValueError(
                    "ENABLE_CALENDAR_CONNECTIONS requires a versioned Calendar client "
                    "secret resource"
                )
            if not self.calendar_redirect_uri.startswith("https://"):
                raise ValueError("ENABLE_CALENDAR_CONNECTIONS requires an HTTPS redirect URI")
            if len(self.calendar_oauth_signing_key.encode("utf-8")) < 32:
                raise ValueError(
                    "ENABLE_CALENDAR_CONNECTIONS requires CALENDAR_OAUTH_SIGNING_KEY (32+ bytes)"
                )
        if self.enable_gmail_drafts:
            self.enable_gmail_connections = True
        if self.enable_gmail_connections:
            if not self.gmail_client_id:
                raise ValueError("ENABLE_GMAIL_CONNECTIONS requires GMAIL_CLIENT_ID")
            if (
                not self.gmail_client_secret_resource_name.startswith(
                    f"projects/{self.google_cloud_project}/secrets/"
                )
                or "/versions/" not in self.gmail_client_secret_resource_name
            ):
                raise ValueError(
                    "ENABLE_GMAIL_CONNECTIONS requires a versioned Gmail client secret resource"
                )
            if not self.gmail_redirect_uri.startswith("https://"):
                raise ValueError("ENABLE_GMAIL_CONNECTIONS requires an HTTPS redirect URI")
            if len(self.gmail_oauth_signing_key.encode("utf-8")) < 32:
                raise ValueError(
                    "ENABLE_GMAIL_CONNECTIONS requires GMAIL_OAUTH_SIGNING_KEY (32+ bytes)"
                )
        if (self.enable_calendar_connections or self.enable_gmail_connections) and not (
            self.oauth_refresh_tokens_secret_resource_name.startswith(
                f"projects/{self.google_cloud_project}/secrets/"
            )
        ):
            raise ValueError(
                "Google OAuth connections require an in-project refresh-token secret resource"
            )
        if self.enable_duffel_quotes and not (
            self.duffel_access_token
            or self.duffel_access_token_secret_resource_name.startswith(
                f"projects/{self.google_cloud_project}/secrets/"
            )
        ):
            raise ValueError(
                "ENABLE_DUFFEL_QUOTES requires DUFFEL_ACCESS_TOKEN or a Secret Manager resource"
            )
        if self.enable_judge_mode and not self.gemini_model_id:
            raise ValueError("ENABLE_JUDGE_MODE requires GEMINI_MODEL_ID")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
