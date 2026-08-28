from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WatchpointKind(StrEnum):
    FLIGHT_STATUS = "FLIGHT_STATUS"
    AIRPORT_DISRUPTION = "AIRPORT_DISRUPTION"
    GROUND_TRANSFER = "GROUND_TRANSFER"
    HOTEL_STATUS = "HOTEL_STATUS"
    ACTIVITY_STATUS = "ACTIVITY_STATUS"
    WEATHER_IMPACT = "WEATHER_IMPACT"


class SourceTrust(StrEnum):
    OFFICIAL = "OFFICIAL"
    PUBLIC_SIGNAL = "PUBLIC_SIGNAL"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(UTC)


class TripWatchpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    watchpoint_id: str = Field(min_length=1, max_length=300)
    trip_id: str = Field(min_length=1, max_length=200)
    item_id: str | None = Field(default=None, max_length=200)
    kind: WatchpointKind
    query: str = Field(min_length=8, max_length=500)
    # Gemini's trust label is advisory; only these deterministic hosts may
    # authorize a recovery event for this watchpoint.
    trusted_domains: list[str] = Field(default_factory=list, max_length=8)
    due_at: datetime
    check_interval_minutes: int = Field(default=30, ge=5, le=1440)
    last_checked_at: datetime | None = None
    last_signal_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_code: str | None = Field(default=None, max_length=80)

    _aware_due = field_validator("due_at")(_aware)
    _aware_checked = field_validator("last_checked_at")(_aware)
    _aware_signal = field_validator("last_signal_at")(_aware)
    _aware_error = field_validator("last_error_at")(_aware)

    @field_validator("trusted_domains")
    @classmethod
    def _normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().rstrip(".")
            if not domain or "://" in domain or "/" in domain or " " in domain:
                raise ValueError("trusted_domains must contain hostnames only")
            if domain not in normalized:
                normalized.append(domain)
        return normalized


class GroundedTravelSignal(BaseModel):
    """A cited web fact. It cannot grant recovery authority on its own."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    watchpoint_id: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=1000)
    source_url: str = Field(min_length=8, max_length=2000)
    source_title: str = Field(min_length=1, max_length=300)
    trust: SourceTrust
    source_updated_at: datetime | None = None
    observed_at: datetime
    affects_trip: bool
    suggested_event_type: str | None = Field(default=None, max_length=100)
    # Cause attribution is optional because a public signal may prove a delay without
    # proving who caused it.  Compensation claims must see this explicitly set to true.
    airline_fault: bool | None = None
    observed_flight: str | None = Field(default=None, max_length=50)
    old_arrival: datetime | None = None
    new_arrival: datetime | None = None
    # Operational delivery state. A signal is persisted before Pub/Sub publication
    # so a transient publish failure can be retried without re-querying the source.
    published_at: datetime | None = None

    _aware_source = field_validator("source_updated_at")(_aware)
    _aware_observed = field_validator("observed_at")(_aware)
    _aware_old_arrival = field_validator("old_arrival")(_aware)
    _aware_new_arrival = field_validator("new_arrival")(_aware)
    _aware_published = field_validator("published_at")(_aware)
