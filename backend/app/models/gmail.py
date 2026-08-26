from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class GmailConnectionStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    REVOKED = "REVOKED"


class GmailConnection(BaseModel):
    """Non-sensitive metadata for a least-privilege Gmail connection.

    The Google refresh token is stored as a Secret Manager version. Firestore
    stores only that version name, status, and the explicitly granted scope.
    """

    model_config = ConfigDict(extra="forbid")

    telegram_user_id: str = Field(min_length=1)
    provider: str = "GOOGLE_GMAIL"
    secret_resource_name: str | None = None
    scopes: list[str] = Field(default_factory=list, max_length=8)
    status: GmailConnectionStatus
    created_at: datetime
    updated_at: datetime
    disconnected_at: datetime | None = None


class GmailOAuthState(BaseModel):
    """Single-use OAuth state for the Gmail draft-only connection."""

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
