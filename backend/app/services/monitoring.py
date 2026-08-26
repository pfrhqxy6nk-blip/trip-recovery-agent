from __future__ import annotations

from datetime import datetime

from app.models.domain import DisruptionEvent, Trip
from app.models.enums import ItemType
from app.models.monitoring import (
    MonitoringCoverage,
    MonitoringSubscription,
    ObservationSnapshot,
    ObservationStatus,
)
from app.services.canonical_hash import canonical_hash
from app.services.ports import IncidentRepository


class MonitoringError(ValueError):
    pass


class MonitoringService:
    """Binds observations to known trip items before they can create disruptions."""

    _STORED_SCHEDULE_SOURCE = "stored-schedule-v1"

    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def register_stored_schedule(
        self, trip: Trip, *, now: datetime
    ) -> list[MonitoringSubscription]:
        if trip.owner_user_id is None:
            raise MonitoringError("a monitored trip must have an owner")
        subscriptions: list[MonitoringSubscription] = []
        for item in trip.items:
            if item.type not in {ItemType.FLIGHT, ItemType.HOTEL_ARRIVAL}:
                continue
            subscription = MonitoringSubscription(
                subscription_id=f"monitor:{trip.trip_id}:{item.item_id}",
                trip_id=trip.trip_id,
                item_id=item.item_id,
                owner_user_id=trip.owner_user_id,
                source_id=self._STORED_SCHEDULE_SOURCE,
                coverage=MonitoringCoverage.SCHEDULE_STORED,
                created_at=now,
                updated_at=now,
            )
            await self._repository.put_monitoring_subscription(subscription)
            subscriptions.append(subscription)
        return subscriptions

    async def activate_deterministic_flight_fixture(
        self, *, trip_id: str, item_id: str, owner_user_id: str, now: datetime
    ) -> MonitoringSubscription:
        subscription_id = f"monitor:{trip_id}:{item_id}"
        current = await self._repository.get_monitoring_subscription(subscription_id)
        if current is None or current.owner_user_id != owner_user_id:
            raise MonitoringError("monitoring subscription is not owned by this traveler")
        updated = current.model_copy(
            update={
                "source_id": "deterministic-flight-fixture-v1",
                "coverage": MonitoringCoverage.DETERMINISTIC_FIXTURE,
                "updated_at": now,
            }
        )
        if not await self._repository.update_monitoring_subscription(
            updated, expected_fingerprint=current.last_snapshot_fingerprint
        ):
            raise MonitoringError("monitoring subscription changed; reload before activation")
        return updated

    async def activate_live_flight_status(
        self, *, trip_id: str, item_id: str, owner_user_id: str, now: datetime
    ) -> MonitoringSubscription:
        """Bind an owned flight to the production status provider.

        The binding is explicit and compare-and-set protected.  A scheduler can
        therefore never turn an arbitrary itinerary item into a live provider
        request merely because it happens to resemble a flight number.
        """

        subscription_id = f"monitor:{trip_id}:{item_id}"
        current = await self._repository.get_monitoring_subscription(subscription_id)
        if current is None or current.owner_user_id != owner_user_id:
            raise MonitoringError("monitoring subscription is not owned by this traveler")
        updated = current.model_copy(
            update={
                "source_id": "amadeus-flight-status-v1",
                "coverage": MonitoringCoverage.LIVE_STATUS,
                "updated_at": now,
            }
        )
        if not await self._repository.update_monitoring_subscription(
            updated, expected_fingerprint=current.last_snapshot_fingerprint
        ):
            raise MonitoringError("monitoring subscription changed; reload before activation")
        return updated

    async def mark_live_status_degraded(self, *, subscription_id: str, now: datetime) -> bool:
        """Record a failed live check without treating it as an on-time result."""

        current = await self._repository.get_monitoring_subscription(subscription_id)
        if current is None:
            return False
        if current.coverage == MonitoringCoverage.MONITORING_DEGRADED:
            return True
        updated = current.model_copy(
            update={
                "coverage": MonitoringCoverage.MONITORING_DEGRADED,
                "updated_at": now,
            }
        )
        return await self._repository.update_monitoring_subscription(
            updated, expected_fingerprint=current.last_snapshot_fingerprint
        )

    async def ingest_snapshot(self, snapshot: ObservationSnapshot) -> DisruptionEvent | None:
        subscription = await self._repository.get_monitoring_subscription(snapshot.subscription_id)
        if subscription is None or subscription.source_id != snapshot.source_id:
            raise MonitoringError("observation source is not authorized for this itinerary item")
        if subscription.coverage not in {
            MonitoringCoverage.DETERMINISTIC_FIXTURE,
            MonitoringCoverage.LIVE_STATUS,
            MonitoringCoverage.MONITORING_DEGRADED,
        }:
            raise MonitoringError("this itinerary item has no active status source")
        trip = await self._repository.get_trip(subscription.trip_id)
        if trip is None or trip.owner_user_id != subscription.owner_user_id:
            raise MonitoringError("observation trip ownership cannot be verified")
        item = next(
            (candidate for candidate in trip.items if candidate.item_id == subscription.item_id),
            None,
        )
        if item is None or item.type != ItemType.FLIGHT:
            raise MonitoringError("only a bound flight item may emit a flight observation")
        if snapshot.scheduled_arrival != item.end_at:
            raise MonitoringError("observation schedule does not match the stored itinerary")

        fingerprint = canonical_hash(snapshot)
        if fingerprint == subscription.last_snapshot_fingerprint:
            return None
        updated = subscription.model_copy(
            update={
                "source_updated_at": snapshot.source_updated_at,
                "last_checked_at": snapshot.observed_at,
                "last_snapshot_fingerprint": fingerprint,
                "coverage": MonitoringCoverage.LIVE_STATUS
                if subscription.source_id == "amadeus-flight-status-v1"
                else MonitoringCoverage.DETERMINISTIC_FIXTURE,
                "updated_at": snapshot.observed_at,
            }
        )
        if not await self._repository.update_monitoring_subscription(
            updated, expected_fingerprint=subscription.last_snapshot_fingerprint
        ):
            return None
        if snapshot.status != ObservationStatus.DELAYED or snapshot.observed_arrival is None:
            return None
        if snapshot.observed_arrival <= snapshot.scheduled_arrival:
            return None
        return DisruptionEvent(
            event_id=f"observation-{fingerprint[:40]}",
            trip_id=trip.trip_id,
            type="FLIGHT_ARRIVAL_DELAY",
            flight=item.external_id or item.provider,
            old_arrival=item.end_at,
            new_arrival=snapshot.observed_arrival,
            context={
                "source_id": snapshot.source_id,
                "source_updated_at": snapshot.source_updated_at.isoformat(),
                "provider_event_id": snapshot.provider_event_id,
                "observation_fingerprint": fingerprint,
            },
        )

    @staticmethod
    def coverage_label(subscription: MonitoringSubscription) -> str:
        labels = {
            MonitoringCoverage.SCHEDULE_STORED: "Schedule stored — no live source connected",
            MonitoringCoverage.DETERMINISTIC_FIXTURE: "Demo source — controlled test only",
            MonitoringCoverage.LIVE_STATUS: "Live status — source recently observed",
            MonitoringCoverage.MONITORING_DEGRADED: "Monitoring degraded — status is unknown",
        }
        return labels[subscription.coverage]
