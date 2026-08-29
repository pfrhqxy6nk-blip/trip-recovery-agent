from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MonitoringCoverage(StrEnum):
    SCHEDULE_STORED = "SCHEDULE_STORED"
    DETERMINISTIC_FIXTURE = "DETERMINISTIC_FIXTURE"
    LIVE_STATUS = "LIVE_STATUS"
    MONITORING_DEGRADED = "MONITORING_DEGRADED"


class ObservationStatus(StrEnum):
    ON_TIME = "ON_TIME"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(UTC)


class MonitoringSubscription(BaseModel):
    """One allowed observation source for one owned itinerary item."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    subscription_id: str = Field(min_length=1, max_length=300)
    trip_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=100)
    coverage: MonitoringCoverage
    source_updated_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_snapshot_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    created_at: datetime
    updated_at: datetime

    _aware_source = field_validator("source_updated_at")(_aware)
    _aware_checked = field_validator("last_checked_at")(_aware)
    _aware_created = field_validator("created_at")(_aware)
    _aware_updated = field_validator("updated_at")(_aware)


class ObservationSnapshot(BaseModel):
    """Untrusted normalized provider observation, bound to one subscription."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    subscription_id: str = Field(min_length=1, max_length=300)
    source_id: str = Field(min_length=1, max_length=100)
    status: ObservationStatus
    scheduled_arrival: datetime
    observed_arrival: datetime | None = None
    source_updated_at: datetime
    observed_at: datetime
    provider_event_id: str = Field(min_length=1, max_length=200)

    _aware_scheduled = field_validator("scheduled_arrival")(_aware)
    _aware_observed_arrival = field_validator("observed_arrival")(_aware)
    _aware_source_updated = field_validator("source_updated_at")(_aware)
    _aware_observed = field_validator("observed_at")(_aware)
