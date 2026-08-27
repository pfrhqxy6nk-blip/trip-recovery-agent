from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AiConnectionStatus(StrEnum):
    PENDING = "PENDING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    INVALID = "INVALID"


class AiProviderSelector(StrEnum):
    USER_MANAGED_GEMINI = "USER_MANAGED_GEMINI"
    SYSTEM_VERTEX = "SYSTEM_VERTEX"


class AiConnectionHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_hash: str = Field(min_length=64, max_length=64)
    telegram_user_id: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None


class AiConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram_user_id: str = Field(min_length=1)
    provider: str = "GEMINI_API"
    mode: str = "USER_MANAGED_KEY"
    selector: AiProviderSelector = AiProviderSelector.USER_MANAGED_GEMINI
    secret_resource_name: str | None = None
    key_fingerprint: str | None = None
    status: AiConnectionStatus
    created_at: datetime
    validated_at: datetime | None = None
    disconnected_at: datetime | None = None
