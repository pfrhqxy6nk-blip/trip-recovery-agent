from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ActionSafetyClass,
    ActionStatus,
    DependencyType,
    IncidentStatus,
    ItemType,
    TripStatus,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(UTC)


class DisruptionEvent(DomainModel):
    event_id: str = Field(min_length=1, max_length=200)
    trip_id: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=100)
    flight: str = Field(min_length=1, max_length=50)
    old_arrival: datetime
    new_arrival: datetime
    context: dict[str, Any] = Field(default_factory=dict)

    _aware_old = field_validator("old_arrival")(_require_aware)
    _aware_new = field_validator("new_arrival")(_require_aware)


class TravelItem(DomainModel):
    item_id: str
    trip_id: str
    type: ItemType
    provider: str
    start_at: datetime
    end_at: datetime
    origin: str | None = None
    destination: str | None = None
    location: str | None = None
    external_id: str | None = None
    flexibility: str = "UNKNOWN"
    status: str = "CONFIRMED"

    _aware_start = field_validator("start_at")(_require_aware)
    _aware_end = field_validator("end_at")(_require_aware)


class Dependency(DomainModel):
    dependency_id: str
    trip_id: str
    from_item_id: str
    to_item_id: str
    type: DependencyType
    min_buffer_minutes: int = Field(default=0, ge=0)


class Trip(DomainModel):
    trip_id: str
    status: TripStatus = TripStatus.HEALTHY
    origin: str
    destination: str
    starts_at: datetime
    ends_at: datetime
    items: list[TravelItem] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _aware_starts = field_validator("starts_at")(_require_aware)
    _aware_ends = field_validator("ends_at")(_require_aware)


class BufferViolation(DomainModel):
    dependency_id: str
    available_minutes: int
    required_minutes: int


class DeterministicImpact(DomainModel):
    disrupted_item_id: str
    arrival_delta_minutes: int
    connection_feasible: bool
    affected_item_ids: list[str]
    affected_dependency_ids: list[str]
    buffer_violations: list[BufferViolation]
    calculated_at: datetime = Field(default_factory=utc_now)
    engine_version: str = "impact-engine-v1"


class TravelInterpretation(DomainModel):
    normalized_event_type: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=1000)
    contextual_factors: list[str] = Field(default_factory=list, max_length=12)
    explanation: str = Field(min_length=1, max_length=3000)
    confidence: float = Field(ge=0, le=1)


class Incident(DomainModel):
    incident_id: str
    trip_id: str
    external_event_id: str
    correlation_id: str
    trigger: DisruptionEvent
    status: IncidentStatus = IncidentStatus.RECEIVED
    version: int = Field(default=1, ge=1)
    deterministic_impact: DeterministicImpact | None = None
    interpretation: TravelInterpretation | None = None
    gemini_model_id: str | None = None
    prompt_version: str | None = None
    last_error: str | None = None
    retry_count: int = Field(default=0, ge=0)
    next_retry_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    analysis_started_at: datetime | None = None
    analysis_completed_at: datetime | None = None


class Action(DomainModel):
    action_id: str
    incident_id: str
    plan_version: int = Field(ge=1)
    type: str
    target_external_id: str
    safety_class: ActionSafetyClass
    status: ActionStatus = ActionStatus.PENDING
    idempotency_key: str

    @classmethod
    def stable_key(
        cls, incident_id: str, plan_version: int, action_type: str, target_external_id: str
    ) -> str:
        return f"{incident_id}:{plan_version}:{action_type}:{target_external_id}"


class Approval(DomainModel):
    approval_id: str
    incident_id: str
    plan_version: int = Field(ge=1)
    plan_hash: str
    quoted_cost: Decimal
    currency: str = Field(min_length=3, max_length=3)
    expires_at: datetime

    _aware_expiry = field_validator("expires_at")(_require_aware)
