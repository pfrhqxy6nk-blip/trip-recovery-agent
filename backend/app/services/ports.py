from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.models.domain import DisruptionEvent, Incident, Trip
from app.models.enums import ClaimKind


class ClaimResult:
    def __init__(self, kind: ClaimKind, incident_id: str) -> None:
        self.kind = kind
        self.incident_id = incident_id

    @property
    def acquired(self) -> bool:
        return self.kind in {ClaimKind.NEW, ClaimKind.RESUMED}


class IncidentRepository(Protocol):
    async def seed_trip(self, trip: Trip) -> None: ...

    async def get_trip(self, trip_id: str) -> Trip | None: ...

    async def claim_event(
        self,
        *,
        event: DisruptionEvent,
        incident: Incident,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimResult: ...

    async def get_incident(self, incident_id: str) -> Incident | None: ...

    async def save_incident(self, incident: Incident) -> None: ...

    async def mark_event_completed(self, event_id: str, completed_at: datetime) -> None: ...

    async def mark_event_retryable(
        self, event_id: str, incident: Incident, error: str, failed_at: datetime
    ) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, event: DisruptionEvent) -> str: ...


class TravelInterpreter(Protocol):
    model_id: str
    prompt_version: str

    async def interpret(
        self, event: DisruptionEvent, trip: Trip, deterministic_impact: Any
    ) -> dict[str, Any]: ...
