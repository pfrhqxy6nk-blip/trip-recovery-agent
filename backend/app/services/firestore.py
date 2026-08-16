from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.domain import DisruptionEvent, Incident, Trip
from app.models.enums import ClaimKind, EventProcessingStatus, IncidentStatus
from app.services.ports import ClaimResult


class FirestoreIncidentRepository:
    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore_v1

        self._firestore = firestore_v1
        self._client = firestore_v1.AsyncClient(project=project_id)

    async def seed_trip(self, trip: Trip) -> None:
        trip_ref = self._client.collection("trips").document(trip.trip_id)
        header = trip.model_dump(mode="python", exclude={"items", "dependencies"})
        batch = self._client.batch()
        batch.set(trip_ref, header, merge=True)
        for item in trip.items:
            batch.set(
                trip_ref.collection("items").document(item.item_id),
                item.model_dump(mode="python"),
                merge=True,
            )
        for dependency in trip.dependencies:
            batch.set(
                trip_ref.collection("dependencies").document(dependency.dependency_id),
                dependency.model_dump(mode="python"),
                merge=True,
            )
        await batch.commit()

    async def get_trip(self, trip_id: str) -> Trip | None:
        trip_ref = self._client.collection("trips").document(trip_id)
        snapshot = await trip_ref.get()
        if not snapshot.exists:
            return None

        items = [doc.to_dict() async for doc in trip_ref.collection("items").stream()]
        dependencies = [
            doc.to_dict() async for doc in trip_ref.collection("dependencies").stream()
        ]
        payload = snapshot.to_dict() or {}
        payload["items"] = items
        payload["dependencies"] = dependencies
        return Trip.model_validate(payload)

    async def claim_event(
        self,
        *,
        event: DisruptionEvent,
        incident: Incident,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimResult:
        event_ref = self._client.collection("processedEvents").document(event.event_id)
        incident_ref = self._client.collection("incidents").document(incident.incident_id)
        transaction = self._client.transaction()

        @self._firestore.async_transactional
        async def claim(transaction: Any) -> ClaimResult:
            event_snapshot = await event_ref.get(transaction=transaction)
            if not event_snapshot.exists:
                transaction.create(
                    event_ref,
                    {
                        "event_id": event.event_id,
                        "incident_id": incident.incident_id,
                        "status": EventProcessingStatus.PROCESSING.value,
                        "lease_owner": worker_id,
                        "lease_expires_at": lease_expires_at,
                        "attempts": 1,
                        "claimed_at": incident.updated_at,
                    },
                )
                transaction.create(incident_ref, incident.model_dump(mode="python"))
                return ClaimResult(ClaimKind.NEW, incident.incident_id)

            record = event_snapshot.to_dict() or {}
            incident_id = str(record["incident_id"])
            status = str(record["status"])
            if status == EventProcessingStatus.COMPLETED.value:
                return ClaimResult(ClaimKind.COMPLETED, incident_id)

            current_lease = record.get("lease_expires_at")
            if (
                status == EventProcessingStatus.PROCESSING.value
                and current_lease is not None
                and current_lease > incident.updated_at
            ):
                return ClaimResult(ClaimKind.IN_PROGRESS, incident_id)

            existing_ref = self._client.collection("incidents").document(incident_id)
            existing_snapshot = await existing_ref.get(transaction=transaction)
            existing = Incident.model_validate(existing_snapshot.to_dict())
            existing.status = IncidentStatus.RECEIVED
            existing.last_error = None
            existing.retry_count += 1
            existing.lease_owner = worker_id
            existing.lease_expires_at = lease_expires_at
            existing.updated_at = incident.updated_at
            existing.version += 1
            transaction.update(
                event_ref,
                {
                    "status": EventProcessingStatus.PROCESSING.value,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                    "attempts": int(record.get("attempts", 1)) + 1,
                },
            )
            transaction.set(existing_ref, existing.model_dump(mode="python"))
            return ClaimResult(ClaimKind.RESUMED, incident_id)

        return await claim(transaction)

    async def get_incident(self, incident_id: str) -> Incident | None:
        snapshot = await self._client.collection("incidents").document(incident_id).get()
        if not snapshot.exists:
            return None
        return Incident.model_validate(snapshot.to_dict())

    async def save_incident(self, incident: Incident) -> None:
        await self._client.collection("incidents").document(incident.incident_id).set(
            incident.model_dump(mode="python")
        )

    async def mark_event_completed(self, event_id: str, completed_at: datetime) -> None:
        event_ref = self._client.collection("processedEvents").document(event_id)
        await event_ref.update(
            {
                "status": EventProcessingStatus.COMPLETED.value,
                "completed_at": completed_at,
                "lease_owner": None,
                "lease_expires_at": None,
            }
        )

    async def mark_event_retryable(
        self, event_id: str, incident: Incident, error: str, failed_at: datetime
    ) -> None:
        event_ref = self._client.collection("processedEvents").document(event_id)
        incident_ref = self._client.collection("incidents").document(incident.incident_id)
        batch = self._client.batch()
        batch.update(
            event_ref,
            {
                "status": EventProcessingStatus.RETRYABLE.value,
                "last_error": error,
                "failed_at": failed_at,
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
        batch.set(incident_ref, incident.model_dump(mode="python"))
        await batch.commit()
