from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.planning import PlanningRequest, TravelPlanContext, TravelPlanOption

_IATA_AIRPORT = re.compile(r"[A-Z]{3}")
_FLIGHT_NUMBER = re.compile(r"[A-Z0-9]{2,8}")
_REFERENCE = re.compile(r"[A-Z0-9-]{2,80}")
_PROVIDER = re.compile(r"[A-Za-z0-9 .,&'()-]{1,80}")
_TERMINAL = re.compile(r"[A-Za-z0-9 -]{1,12}")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def _clean(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or any(
        ord(character) < 32 or ord(character) == 127 for character in normalized
    ):
        raise ValueError("value contains unsupported control characters")
    return normalized


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value


class FlightImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_number: str = Field(min_length=2, max_length=20)
    provider: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=3, max_length=8)
    destination: str = Field(min_length=3, max_length=8)
    departure_at: datetime
    arrival_at: datetime
    booking_reference: str | None = Field(default=None, min_length=2, max_length=80)
    departure_terminal: str | None = Field(default=None, max_length=12)
    arrival_terminal: str | None = Field(default=None, max_length=12)

    _aware_departure = field_validator("departure_at")(_aware)
    _aware_arrival = field_validator("arrival_at")(_aware)

    @field_validator("flight_number")
    @classmethod
    def validate_flight_number(cls, value: str) -> str:
        normalized = _clean(value).upper().replace(" ", "")
        if not _FLIGHT_NUMBER.fullmatch(normalized):
            raise ValueError("flight number must contain only letters and numbers")
        return normalized

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = _clean(value)
        if not _PROVIDER.fullmatch(normalized):
            raise ValueError("provider contains unsupported characters")
        return normalized

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        normalized = _clean(value).upper()
        if not _IATA_AIRPORT.fullmatch(normalized):
            raise ValueError("airport must be a three-letter IATA code")
        return normalized

    @field_validator("booking_reference")
    @classmethod
    def validate_booking_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _clean(value).upper()
        if not _REFERENCE.fullmatch(normalized):
            raise ValueError("booking reference contains unsupported characters")
        return normalized

    @field_validator("departure_terminal", "arrival_terminal")
    @classmethod
    def validate_terminal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _clean(value).upper()
        if not _TERMINAL.fullmatch(normalized):
            raise ValueError("terminal contains unsupported characters")
        return normalized

    @model_validator(mode="after")
    def validate_times(self) -> FlightImport:
        if self.arrival_at <= self.departure_at:
            raise ValueError("flight arrival must be after departure")
        return self


class HotelImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    check_in_at: datetime
    check_out_at: datetime
    booking_reference: str | None = Field(default=None, min_length=2, max_length=80)
    contact_email: str | None = Field(default=None, max_length=254)

    _aware_check_in = field_validator("check_in_at")(_aware)
    _aware_check_out = field_validator("check_out_at")(_aware)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = _clean(value)
        if not _PROVIDER.fullmatch(normalized):
            raise ValueError("provider contains unsupported characters")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _clean(value)

    @field_validator("booking_reference")
    @classmethod
    def validate_booking_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _clean(value).upper()
        if not _REFERENCE.fullmatch(normalized):
            raise ValueError("booking reference contains unsupported characters")
        return normalized

    @field_validator("contact_email")
    @classmethod
    def validate_contact_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _clean(value).casefold()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError("hotel contact email is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_times(self) -> HotelImport:
        if self.check_out_at <= self.check_in_at:
            raise ValueError("hotel check-out must be after check-in")
        return self


class TripImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flights: list[FlightImport] = Field(default_factory=list, max_length=8)
    hotel: HotelImport | None = None
    minimum_connection_minutes: int = Field(default=45, ge=0, le=360)

    @model_validator(mode="after")
    def validate_itinerary(self) -> TripImportRequest:
        if not self.flights and self.hotel is None:
            raise ValueError("itinerary must contain at least one flight or hotel")
        for current, following in zip(self.flights, self.flights[1:], strict=False):
            if current.destination.upper() != following.origin.upper():
                raise ValueError("adjacent flights must connect at the same airport")
            if following.departure_at <= current.arrival_at:
                raise ValueError("following flight must depart after the previous arrival")
        if (
            self.hotel is not None
            and self.flights
            and self.hotel.check_in_at < self.flights[-1].arrival_at
        ):
            raise ValueError("hotel check-in cannot be before final flight arrival")
        return self


class TripImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: str
    created: bool
    item_count: int
    dependency_count: int


class TripSourceFile(BaseModel):
    """Non-sensitive provenance for a forwarded itinerary document."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(min_length=1, max_length=120)
    kind: Literal["FLIGHT_TICKET", "BOARDING_PASS", "HOTEL_CONFIRMATION", "OTHER"]
    received_at: datetime

    _aware_received = field_validator("received_at")(_aware)


class TripDraft(BaseModel):
    """A private, explicitly confirmed-in-progress Telegram itinerary."""

    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=200)
    telegram_user_id: str = Field(min_length=1, max_length=100)
    telegram_chat_id: str = Field(min_length=1, max_length=100)
    flights: list[FlightImport] = Field(default_factory=list, max_length=8)
    hotel: HotelImport | None = None
    source_files: list[TripSourceFile] = Field(default_factory=list, max_length=8)
    planning_context: TravelPlanContext | None = None
    planning_request: PlanningRequest | None = None
    planning_options: list[TravelPlanOption] = Field(default_factory=list, max_length=3)
    selected_plan_id: str | None = Field(default=None, min_length=1, max_length=40)
    planning_saved_at: datetime | None = None
    version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
