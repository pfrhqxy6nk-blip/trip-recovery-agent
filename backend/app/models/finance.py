from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.domain import _require_aware
from app.models.money import Money


class OpenFinancialItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    financial_item_id: str = Field(min_length=1, max_length=200)
    trip_id: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=200)
    kind: Literal["REFUND", "DEPOSIT", "REIMBURSEMENT"]
    provider: str = Field(min_length=1, max_length=200)
    expected_amount: Money
    status: Literal["OPEN", "SETTLED", "NEEDS_ATTENTION"] = "OPEN"
    due_at: datetime | None = None
    settled_at: datetime | None = None
    actual_amount: Money | None = None
    created_at: datetime
    updated_at: datetime

    _aware_due = field_validator("due_at")(_require_aware)
    _aware_settled = field_validator("settled_at")(_require_aware)
    _aware_created = field_validator("created_at")(_require_aware)
    _aware_updated = field_validator("updated_at")(_require_aware)

    @model_validator(mode="after")
    def settlement_is_complete(self) -> OpenFinancialItem:
        if self.status == "SETTLED" and (self.settled_at is None or self.actual_amount is None):
            raise ValueError("settled financial item requires time and actual amount")
        return self


class TripClosureReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trip_id: str
    status: Literal["BLOCKED", "CAN_CLOSE", "CLOSED"]
    open_financial_item_ids: tuple[str, ...]
    blockers: tuple[str, ...]
    generated_at: datetime

    _aware_generated = field_validator("generated_at")(_require_aware)
