from datetime import UTC, datetime

import pytest
from app.agents.google_search_watch import WatchProviderError
from app.agents.live_flight_watch import AmadeusFlightWatch
from app.models.domain import TravelItem
from app.models.monitoring import ObservationSnapshot, ObservationStatus
from app.models.watch import TripWatchpoint
from app.providers.amadeus import AmadeusFlightStatusError
from app.services.memory import InMemoryIncidentRepository
from app.services.monitoring import MonitoringService
from app.services.trip_intake import TripIntakeService

from tests.test_monitoring import request


class FakeAmadeusClient:
    async def fetch_snapshot(
        self, *, subscription_id: str, source_id: str, item: TravelItem, observed_at: datetime
    ) -> ObservationSnapshot:
        return ObservationSnapshot(
            subscription_id=subscription_id,
            source_id="amadeus-flight-status-v1",
            status=ObservationStatus.DELAYED,
            scheduled_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
            observed_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
            source_updated_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
            observed_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
            provider_event_id="amadeus-delay-1",
        )


class FailingAmadeusClient:
    async def fetch_snapshot(
        self, *, subscription_id: str, source_id: str, item: TravelItem, observed_at: datetime
    ) -> ObservationSnapshot:
        raise AmadeusFlightStatusError("provider unavailable")


class UnexpectedAmadeusClient:
    async def fetch_snapshot(
        self, *, subscription_id: str, source_id: str, item: TravelItem, observed_at: datetime
    ) -> ObservationSnapshot:
        raise RuntimeError("transport failure")


async def test_live_flight_watch_converts_provider_fact_to_cited_signal() -> None:
    repository = InMemoryIncidentRepository()
    imported = await TripIntakeService(repository).import_trip(
        request(), owner_user_id="telegram:101"
    )
    trip = await repository.get_trip(imported.trip_id)
    assert trip is not None
    monitoring = MonitoringService(repository)
    await monitoring.register_stored_schedule(trip, now=datetime(2026, 8, 1, tzinfo=UTC))
    flight_item = next(item for item in trip.items if item.type.value == "FLIGHT")
    await monitoring.activate_live_flight_status(
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )
    watchpoint = TripWatchpoint(
        watchpoint_id=f"watch:{trip.trip_id}:flight-1:flight_status",
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        kind="FLIGHT_STATUS",
        query="LO351 flight status delay",
        trusted_domains=["api.amadeus.com"],
        due_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
    )

    signal = await AmadeusFlightWatch(
        repository,
        FakeAmadeusClient(),
        clock=lambda: datetime(2026, 8, 20, 16, tzinfo=UTC),
    ).observe(watchpoint)

    assert signal is not None
    assert signal.source_url.startswith("https://api.amadeus.com/")
    assert signal.suggested_event_type == "FLIGHT_ARRIVAL_DELAY"
    assert signal.observed_flight == "LO351"


async def test_failed_live_check_marks_monitoring_degraded_instead_of_on_time() -> None:
    repository = InMemoryIncidentRepository()
    imported = await TripIntakeService(repository).import_trip(
        request(), owner_user_id="telegram:101"
    )
    trip = await repository.get_trip(imported.trip_id)
    assert trip is not None
    monitoring = MonitoringService(repository)
    await monitoring.register_stored_schedule(trip, now=datetime(2026, 8, 1, tzinfo=UTC))
    flight_item = next(item for item in trip.items if item.type.value == "FLIGHT")
    active = await monitoring.activate_live_flight_status(
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )
    watchpoint = TripWatchpoint(
        watchpoint_id=f"watch:{trip.trip_id}:flight-1:flight_status",
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        kind="FLIGHT_STATUS",
        query="LO351 flight status delay",
        trusted_domains=["api.amadeus.com"],
        due_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
    )

    with pytest.raises(WatchProviderError, match="AMADEUS_PROVIDER_ERROR"):
        await AmadeusFlightWatch(
            repository,
            FailingAmadeusClient(),
            clock=lambda: datetime(2026, 8, 20, 16, tzinfo=UTC),
        ).observe(watchpoint)

    degraded = await repository.get_monitoring_subscription(active.subscription_id)
    assert degraded is not None
    assert degraded.coverage.value == "MONITORING_DEGRADED"


async def test_unbound_amadeus_watchpoint_is_not_reported_as_healthy() -> None:
    repository = InMemoryIncidentRepository()
    imported = await TripIntakeService(repository).import_trip(
        request(), owner_user_id="telegram:101"
    )
    trip = await repository.get_trip(imported.trip_id)
    assert trip is not None
    flight_item = next(item for item in trip.items if item.type.value == "FLIGHT")
    watchpoint = TripWatchpoint(
        watchpoint_id=f"watch:{trip.trip_id}:flight-1:flight_status",
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        kind="FLIGHT_STATUS",
        query="LO351 flight status delay",
        trusted_domains=["api.amadeus.com"],
        due_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
    )

    with pytest.raises(WatchProviderError, match="AMADEUS_SUBSCRIPTION_NOT_BOUND"):
        await AmadeusFlightWatch(repository, FakeAmadeusClient()).observe(watchpoint)


async def test_unwrapped_amadeus_failure_is_bounded_and_degrades_live_coverage() -> None:
    repository = InMemoryIncidentRepository()
    imported = await TripIntakeService(repository).import_trip(
        request(), owner_user_id="telegram:101"
    )
    trip = await repository.get_trip(imported.trip_id)
    assert trip is not None
    monitoring = MonitoringService(repository)
    await monitoring.register_stored_schedule(trip, now=datetime(2026, 8, 1, tzinfo=UTC))
    flight_item = next(item for item in trip.items if item.type.value == "FLIGHT")
    active = await monitoring.activate_live_flight_status(
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )
    watchpoint = TripWatchpoint(
        watchpoint_id=f"watch:{trip.trip_id}:flight-1:flight_status",
        trip_id=trip.trip_id,
        item_id=flight_item.item_id,
        kind="FLIGHT_STATUS",
        query="LO351 flight status delay",
        trusted_domains=["api.amadeus.com"],
        due_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
    )

    with pytest.raises(WatchProviderError, match="AMADEUS_PROVIDER_ERROR"):
        await AmadeusFlightWatch(
            repository,
            UnexpectedAmadeusClient(),
            clock=lambda: datetime(2026, 8, 20, 16, tzinfo=UTC),
        ).observe(watchpoint)

    degraded = await repository.get_monitoring_subscription(active.subscription_id)
    assert degraded is not None
    assert degraded.coverage.value == "MONITORING_DEGRADED"
