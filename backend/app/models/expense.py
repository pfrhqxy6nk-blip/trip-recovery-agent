from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain import _require_aware
from app.models.money import Money

ExpenseCategory = Literal["FLIGHT", "HOTEL", "TRANSPORT", "FOOD", "ACTIVITY", "OTHER"]
ExpenseSource = Literal["RECOVERY_ACTION", "TELEGRAM_TEXT", "RECEIPT"]


class TripExpense(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_id: str = Field(min_length=1, max_length=200)
    trip_id: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=200)
    amount: Money
    category: ExpenseCategory
    source: ExpenseSource
    merchant: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    incident_id: str | None = Field(default=None, max_length=200)
    source_effect_key: str | None = Field(default=None, max_length=500)
    telegram_file_id: str | None = Field(default=None, max_length=500)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)
    occurred_at: datetime
    created_at: datetime

    _aware_occurred = field_validator("occurred_at")(_require_aware)
    _aware_created = field_validator("created_at")(_require_aware)
