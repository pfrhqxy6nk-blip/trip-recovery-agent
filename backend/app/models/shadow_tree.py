from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.money import Money


class RiskLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HoldType(StrEnum):
    FARE_LOCK = "FARE_LOCK"
    COURTESY_HOLD = "COURTESY_HOLD"
    HOTEL_STANDBY = "HOTEL_STANDBY"


class HoldStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PROMOTED = "PROMOTED"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class DisruptionRiskAssessment(BaseModel):
    """Predictive Bayesian risk assessment of a travel connection before official disruption."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: str
    from_item_id: str
    to_item_id: str
    hub_airport: str
    scheduled_buffer_minutes: int
    required_buffer_minutes: int
    probability_of_miss: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    risk_factors: list[str] = Field(default_factory=list)
    assessed_at: datetime


class ShadowHold(BaseModel):
    """Preemptively secured zero-cost hold placed before disruption hits the traveler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hold_id: str
    trip_id: str
    connection_id: str
    provider: str
    hold_type: HoldType
    status: HoldStatus = HoldStatus.ACTIVE
    alternative_flight: str
    alternative_origin: str
    alternative_destination: str
    alternative_departure_at: datetime
    alternative_arrival_at: datetime
    expires_at: datetime
    cost_to_hold: Money = Field(default_factory=lambda: Money(currency="EUR", minor_units=0))
    locked_rebooking_price: Money
    surge_market_price: Money
    created_at: datetime
    promoted_at: datetime | None = None
    provider_hold_token: str | None = None


class ShadowExecutionTree(BaseModel):
    """Authoritative predictive tree of hot contingency options and active fare locks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trip_id: str
    assessments: list[DisruptionRiskAssessment] = Field(default_factory=list)
    active_holds: list[ShadowHold] = Field(default_factory=list)
    contingency_summary: str = ""
    updated_at: datetime
