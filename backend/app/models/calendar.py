from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CalendarConnectionStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    REVOKED = "REVOKED"


class CalendarConnection(BaseModel):
    """Metadata for a user's Google Calendar connection.

    Refresh tokens are never part of this model. They live in Secret Manager;
    Firestore only stores the resource name and non-sensitive connection state.
    """

    model_config = ConfigDict(extra="forbid")

    telegram_user_id: str = Field(min_length=1)
    provider: str = "GOOGLE_CALENDAR"
    calendar_id: str = Field(default="primary", min_length=1, max_length=256)
    secret_resource_name: str | None = None
    scopes: list[str] = Field(default_factory=list, max_length=8)
    status: CalendarConnectionStatus
    created_at: datetime
    updated_at: datetime
    disconnected_at: datetime | None = None


class CalendarOAuthState(BaseModel):
    """Single-use OAuth state bound to the Telegram identity and redirect URI."""

    model_config = ConfigDict(extra="forbid")

    state_hash: str = Field(min_length=64, max_length=64)
    telegram_user_id: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    scopes: list[str] = Field(min_length=1, max_length=8)
    code_verifier_hash: str = Field(min_length=64, max_length=64)
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
