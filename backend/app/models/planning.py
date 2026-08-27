from __future__ import annotations

from datetime import date, datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TravelPlanRequest(BaseModel):
    """The small, explicit input needed to create a useful trip plan."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=2, max_length=120)
    origin: str | None = Field(default=None, min_length=2, max_length=120)
    start_date: date
    end_date: date
    budget_eur: int = Field(ge=50, le=100_000)
    interests: list[str] = Field(default_factory=list, max_length=8)
    travelers: int = Field(default=1, ge=1, le=12)

    @field_validator("destination")
    @classmethod
    def clean_destination(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("destination is required")
        return normalized

    @field_validator("origin")
    @classmethod
    def clean_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split()).strip()
        return normalized or None

    @field_validator("interests")
    @classmethod
    def clean_interests(cls, value: list[str]) -> list[str]:
        return [" ".join(item.split()).strip()[:50] for item in value if item.strip()][:8]

    @model_validator(mode="after")
    def validate_dates(self) -> TravelPlanRequest:
        if self.end_date <= self.start_date:
            raise ValueError("end date must be after start date")
        if (self.end_date - self.start_date).days > 60:
            raise ValueError("trip may be at most 60 days")
        return self


class FlexibleTravelPlanRequest(BaseModel):
    """A planning brief with duration and budget but no fixed travel dates yet."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=2, max_length=120)
    origin: str | None = Field(default=None, min_length=2, max_length=120)
    nights: int = Field(ge=1, le=60)
    budget_eur: int = Field(ge=50, le=100_000)
    interests: list[str] = Field(default_factory=list, max_length=8)
    travelers: int = Field(default=1, ge=1, le=12)

    @field_validator("destination")
    @classmethod
    def clean_destination(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("destination is required")
        return normalized

    @field_validator("origin")
    @classmethod
    def clean_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split()).strip()
        return normalized or None

    @field_validator("interests")
    @classmethod
    def clean_interests(cls, value: list[str]) -> list[str]:
        return [" ".join(item.split()).strip()[:50] for item in value if item.strip()][:8]


PlanningRequest: TypeAlias = TravelPlanRequest | FlexibleTravelPlanRequest


class TravelPlanContext(BaseModel):
    """Persisted pieces of a natural-language planning request awaiting one detail."""

    model_config = ConfigDict(extra="forbid")

    destination: str | None = Field(default=None, min_length=2, max_length=120)
    origin: str | None = Field(default=None, min_length=2, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    nights: int | None = Field(default=None, ge=1, le=60)
    budget_eur: int | None = Field(default=None, ge=50, le=100_000)
    interests: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("destination", "origin")
    @classmethod
    def clean_place(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split()).strip()
        return normalized or None

    @field_validator("interests")
    @classmethod
    def clean_interests(cls, value: list[str]) -> list[str]:
        return [" ".join(item.split()).strip()[:50] for item in value if item.strip()][:8]


class TravelPlanTransport(BaseModel):
    """One concrete transport candidate returned by the planning provider.

    These are offers to investigate, never reservations. Keeping the candidate
    structured prevents a language model from hiding a generic route behind a
    polished paragraph and makes the Telegram result useful at a glance.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["FLIGHT", "TRAIN", "BUS"]
    provider: str = Field(min_length=1, max_length=80)
    service: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=2, max_length=80)
    destination: str = Field(min_length=2, max_length=80)
    departure_at: datetime
    arrival_at: datetime
    price_eur: int = Field(ge=0, le=100_000)
    booking_url: str = Field(min_length=12, max_length=500)
    conditions: str = Field(min_length=1, max_length=180)

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("transport times must include a timezone")
        return value

    @field_validator("booking_url")
    @classmethod
    def validate_booking_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("https://"):
            raise ValueError("booking URL must use HTTPS")
        return normalized

    @model_validator(mode="after")
    def validate_times(self) -> TravelPlanTransport:
        if self.arrival_at <= self.departure_at:
            raise ValueError("transport arrival must be after departure")
        return self


class TravelPlanStay(BaseModel):
    """One concrete accommodation candidate for a planning estimate."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    check_in: date
    check_out: date
    nights: int = Field(ge=1, le=60)
    price_eur: int = Field(ge=0, le=100_000)
    cancellation: str = Field(min_length=1, max_length=180)
    booking_url: str = Field(min_length=12, max_length=500)

    @field_validator("booking_url")
    @classmethod
    def validate_booking_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("https://"):
            raise ValueError("booking URL must use HTTPS")
        return normalized

    @model_validator(mode="after")
    def validate_stay(self) -> TravelPlanStay:
        if self.check_out <= self.check_in:
            raise ValueError("check-out must be after check-in")
        if (self.check_out - self.check_in).days != self.nights:
            raise ValueError("stay nights must match check-in and check-out")
        return self


class TravelPlanOption(BaseModel):
    """A sourced, non-booking plan option shown before a traveler commits."""

    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=600)
    route: str = Field(min_length=1, max_length=200)
    estimated_total_eur: int = Field(ge=0, le=100_000)
    travel_time_hours: float = Field(ge=0, le=240)
    resilience_note: str = Field(min_length=1, max_length=240)
    weather_note: str = Field(min_length=1, max_length=240)
    source_links: list[str] = Field(default_factory=list, max_length=8)
    generated_at: datetime
    availability: Literal["ESTIMATE", "LIVE"] = "ESTIMATE"
    # Optional on purpose: old Firestore drafts can still be read. New planner
    # results always populate both fields before they are shown as concrete.
    transport: TravelPlanTransport | None = None
    stay: TravelPlanStay | None = None

    @field_validator("source_links")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for link in value[:8]:
            normalized = link.strip()
            if normalized.startswith("https://") and normalized not in cleaned:
                cleaned.append(normalized[:500])
        return cleaned
