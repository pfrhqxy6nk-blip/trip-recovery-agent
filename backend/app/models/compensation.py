from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.money import Money


class RegulationJurisdiction(StrEnum):
    EU261 = "EU261"  # Regulation (EC) No 261/2004
    UK261 = "UK261"  # UK Air Passenger Rights (Amendment) Regulations 2019
    US_DOT = "US_DOT"  # US Department of Transportation Airline Passenger Protections
    NONE = "NONE"


class DisruptionCategory(StrEnum):
    FLIGHT_DELAY = "flight_delay"
    FLIGHT_CANCELLATION = "flight_cancellation"
    DENIED_BOARDING = "denied_boarding"
    MISSED_CONNECTION = "missed_connection"


class CompensationAssessment(BaseModel):
    """Authoritative legal assessment of flight disruption compensation eligibility."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    jurisdiction: RegulationJurisdiction
    amount: Money | None = None
    distance_km: int = Field(ge=0)
    delay_minutes: int = Field(ge=0)
    disruption_category: DisruptionCategory
    origin: str = Field(min_length=3, max_length=4)
    destination: str = Field(min_length=3, max_length=4)
    airline_code: str = Field(min_length=2, max_length=4)
    airline_name: str
    reasons: list[str] = Field(default_factory=list)
    legal_citations: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    source_timestamps: list[datetime] = Field(default_factory=list)
    claim_ready: bool = False


class ClaimLetter(BaseModel):
    """Reviewable formal legal claim for statutory flight disruption compensation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    incident_id: str
    passenger_name: str
    airline_name: str
    flight_number: str
    booking_reference: str | None = None
    route: str
    origin: str
    destination: str
    scheduled_arrival: datetime
    actual_arrival: datetime
    delay_minutes: int
    distance_km: int
    compensation_amount: Money
    jurisdiction: RegulationJurisdiction
    legal_basis: str
    subject_en: str
    body_en: str
    subject_ru: str
    body_ru: str
    deadline_days: int = 14
    required_attachments: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    evidence_timestamps: list[datetime] = Field(default_factory=list)
    review_required: bool = True
