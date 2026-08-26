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

    @field_validator("source_links")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for link in value[:8]:
            normalized = link.strip()
            if normalized.startswith("https://") and normalized not in cleaned:
                cleaned.append(normalized[:500])
        return cleaned
