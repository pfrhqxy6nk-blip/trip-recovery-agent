from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import OnboardingStep, PolicyMode
from app.models.money import Money


class TravelerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    telegram_user_id: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)
    onboarding_step: OnboardingStep = OnboardingStep.PROMISE
    calendar_mode: PolicyMode = PolicyMode.AUTO
    service_message_mode: PolicyMode = PolicyMode.AUTO
    reversible_change_mode: PolicyMode = PolicyMode.AUTO
    automatic_spending_enabled: bool = False
    incident_spending_limit: Money | None = None
    recommendations_enabled: bool = False
    recommendation_interests: list[str] = Field(default_factory=list, max_length=8)
    active_policy_version: int | None = None
    created_at: datetime
    updated_at: datetime


class TelegramButton(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=128)
    callback_data: str | None = Field(default=None, min_length=1)
    url: str | None = Field(default=None, min_length=1)

    @field_validator("callback_data")
    @classmethod
    def callback_data_fits_telegram_limit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value.encode("utf-8")) > 64:
            raise ValueError("Telegram callback_data must not exceed 64 UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def has_exactly_one_action(self) -> TelegramButton:
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("button requires exactly one of callback_data or url")
        return self


class TelegramView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4096)
    parse_mode: Literal["HTML"] | None = None
    buttons: list[TelegramButton] = Field(default_factory=list)
    button_rows: list[list[TelegramButton]] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_one_keyboard_shape(self) -> TelegramView:
        if self.buttons and self.button_rows:
            raise ValueError("use either buttons or button_rows, not both")
        if any(not row for row in self.button_rows):
            raise ValueError("Telegram inline keyboard rows must not be empty")
        return self

    def inline_keyboard(self) -> list[list[TelegramButton]]:
        """Return explicit rows, or one compatibility row for the older flat API."""

        if self.button_rows:
            return self.button_rows
        return [self.buttons] if self.buttons else []


class TelegramMessageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_id: str = Field(min_length=1)
    message_id: int = Field(ge=1)
    date: int | None = Field(default=None, ge=0)


class TelegramFileDownload(BaseModel):
    """Downloaded Telegram media with bounded, non-sensitive metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str = Field(min_length=1, max_length=200)
    file_name: str | None = Field(default=None, max_length=200)
    mime_type: str | None = Field(default=None, max_length=120)
    content: bytes = Field(min_length=1)


class OutboundNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    kind: Literal["AWARENESS", "APPROVAL", "FINAL", "WATCH_SIGNAL"]
    chat_id: str = Field(min_length=1)
    view_hash: str = Field(min_length=64, max_length=64)
    status: Literal["PENDING", "SENT", "UNKNOWN", "BLOCKED"] = "PENDING"
    message_id: int | None = Field(default=None, ge=1)
    created_at: datetime
    sent_at: datetime | None = None
    unknown_at: datetime | None = None
    blocked_at: datetime | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=80)
