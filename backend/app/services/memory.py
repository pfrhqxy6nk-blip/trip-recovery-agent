from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.models.domain import DisruptionEvent, Incident, Trip
from app.models.enums import ClaimKind, EventProcessingStatus, IncidentStatus
from app.services.ports import ClaimResult


class InMemoryIncidentRepository:
    """Process-local adapter used by tests and explicit local development mode."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.trips: dict[str, Trip] = {}
        self.incidents: dict[str, Incident] = {}
        self.processed_events: dict[str, dict[str, Any]] = {}

    async def seed_trip(self, trip: Trip) -> None:
        async with self._lock:
            self.trips.setdefault(trip.trip_id, deepcopy(trip))

    async def get_trip(self, trip_id: str) -> Trip | None:
        async with self._lock:
            trip = self.trips.get(trip_id)
            return deepcopy(trip) if trip else None

    async def claim_event(
        self,
        *,
        event: DisruptionEvent,
        incident: Incident,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimResult:
        async with self._lock:
            record = self.processed_events.get(event.event_id)
            now = incident.updated_at
            if record is None:
                self.processed_events[event.event_id] = {
                    "event_id": event.event_id,
                    "incident_id": incident.incident_id,
                    "status": EventProcessingStatus.PROCESSING,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                    "attempts": 1,
                    "claimed_at": now,
                }
                incident.lease_owner = worker_id
                incident.lease_expires_at = lease_expires_at
                self.incidents[incident.incident_id] = deepcopy(incident)
                return ClaimResult(ClaimKind.NEW, incident.incident_id)

            incident_id = str(record["incident_id"])
            if record["status"] == EventProcessingStatus.COMPLETED:
                return ClaimResult(ClaimKind.COMPLETED, incident_id)

            current_lease = record.get("lease_expires_at")
            if (
                record["status"] == EventProcessingStatus.PROCESSING
                and current_lease is not None
                and current_lease > now
            ):
                return ClaimResult(ClaimKind.IN_PROGRESS, incident_id)

            record.update(
                status=EventProcessingStatus.PROCESSING,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                attempts=int(record["attempts"]) + 1,
            )
            existing = self.incidents[incident_id]
            existing.lease_owner = worker_id
            existing.lease_expires_at = lease_expires_at
            existing.retry_count += 1
            existing.status = IncidentStatus.RECEIVED
            existing.last_error = None
            existing.updated_at = now
            self.incidents[incident_id] = deepcopy(existing)
            return ClaimResult(ClaimKind.RESUMED, incident_id)

    async def get_incident(self, incident_id: str) -> Incident | None:
        async with self._lock:
            incident = self.incidents.get(incident_id)
            return deepcopy(incident) if incident else None

    async def save_incident(self, incident: Incident) -> None:
        async with self._lock:
            if incident.incident_id not in self.incidents:
                raise KeyError(f"incident {incident.incident_id!r} does not exist")
            self.incidents[incident.incident_id] = deepcopy(incident)

    async def mark_event_completed(self, event_id: str, completed_at: datetime) -> None:
        async with self._lock:
            record = self.processed_events[event_id]
            record.update(
                status=EventProcessingStatus.COMPLETED,
                completed_at=completed_at,
                lease_owner=None,
                lease_expires_at=None,
            )

    async def mark_event_retryable(
        self, event_id: str, incident: Incident, error: str, failed_at: datetime
    ) -> None:
        async with self._lock:
            record = self.processed_events[event_id]
            record.update(
                status=EventProcessingStatus.RETRYABLE,
                last_error=error,
                failed_at=failed_at,
                lease_owner=None,
                lease_expires_at=None,
            )
            self.incidents[incident.incident_id] = deepcopy(incident)


class LocalEventPublisher:
    def __init__(self) -> None:
        self.events: list[DisruptionEvent] = []

    async def publish(self, event: DisruptionEvent) -> str:
        self.events.append(event.model_copy(deep=True))
        return f"local-{len(self.events)}"
