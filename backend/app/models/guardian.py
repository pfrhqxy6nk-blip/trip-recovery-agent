from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VisaScreeningStatus(StrEnum):
    CLEAR = "CLEAR"
    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"
    UNKNOWN = "UNKNOWN"


class BaggageScreeningStatus(StrEnum):
    FEASIBLE_ESTIMATE = "FEASIBLE_ESTIMATE"
    TIGHT_ESTIMATE = "TIGHT_ESTIMATE"
    HIGH_RISK_ESTIMATE = "HIGH_RISK_ESTIMATE"
    UNKNOWN = "UNKNOWN"


class TravelerTravelProfile(BaseModel):
    """Screening profile specifying passport and baggage parameters for a traveler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citizenship_iso2: str = Field(
        default="DE",
        min_length=2,
        max_length=3,
        description="2-letter or 3-letter ISO country code of passport.",
    )
    has_checked_bags: bool = True
    bag_count: int = Field(default=1, ge=0, le=10)


class VisaScreeningResult(BaseModel):
    """Deterministic, non-legal visa screening assessment for a proposed route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: VisaScreeningStatus
    schengen_transit: bool = False
    airside_transit_allowed: bool | None = None
    requires_human_confirmation: bool = False
    notes: list[str] = Field(default_factory=list)
    legal_disclaimer: str = (
        "Screening tool only. Not official immigration or legal advice. "
        "Always verify travel and transit visa requirements with your airline and embassy."
    )


class BaggageScreeningResult(BaseModel):
    """Estimated minimum baggage connection timing and transfer feasibility."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: BaggageScreeningStatus
    scheduled_buffer_minutes: int
    estimated_mbct_minutes: int
    has_checked_bags: bool
    requires_customs_recheck: bool = False
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Estimated baggage transfer timing heuristic. "
        "Actual handling times vary by airline, ground handler, and airport operations."
    )


class TravelScreeningReport(BaseModel):
    """Combined screening report covering transit visa and baggage transfer invariants."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: str
    hub_airport: str
    visa: VisaScreeningResult
    baggage: BaggageScreeningResult
    is_safe_to_reroute_estimate: bool
    summary: str
