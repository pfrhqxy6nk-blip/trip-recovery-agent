from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ActionCategory, PolicyMode, PolicyReasonCode, PolicyVerdict
from app.models.money import Money


class AutonomyPolicy(BaseModel):
    """A versioned traveler policy. Mandatory safety rules live in code, not settings."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    notify_meaningful_changes: bool = True
    calendar_mode: PolicyMode = PolicyMode.AUTO
    service_message_mode: PolicyMode = PolicyMode.AUTO
    reversible_change_mode: PolicyMode = PolicyMode.AUTO
    automatic_spending_enabled: bool = False
    incident_spending_limit: Money | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_spending_authority(self) -> AutonomyPolicy:
        if self.automatic_spending_enabled and self.incident_spending_limit is None:
            raise ValueError("automatic spending requires an incident spending limit")
        if self.incident_spending_limit is not None:
            if self.incident_spending_limit.currency != "EUR":
                raise ValueError("the MVP automatic spending currency is EUR")
            if self.incident_spending_limit.minor_units < 0:
                raise ValueError("incident spending limit must not be negative")
        return self


class PolicyCandidate(BaseModel):
    """Safety facts normalized from provider data before an authority decision."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    category: ActionCategory
    cost: Money = Field(default_factory=lambda: Money(currency="EUR", minor_units=0))
    reversible: bool = False
    penalty_minor_units: int = Field(default=0, ge=0)
    ambiguous: bool = False
    major_change_reasons: list[str] = Field(default_factory=list)

    @field_validator("major_change_reasons")
    @classmethod
    def normalize_reasons(cls, value: list[str]) -> list[str]:
        return sorted({reason.strip() for reason in value if reason.strip()})


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    verdict: PolicyVerdict
    reason_codes: tuple[PolicyReasonCode, ...]
    remaining_automatic_spend: Money | None = None
