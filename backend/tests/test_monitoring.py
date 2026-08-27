from datetime import UTC, datetime, timedelta

import pytest
from app.models.monitoring import ObservationSnapshot, ObservationStatus
from app.models.trip_intake import FlightImport, TripImportRequest
from app.services.memory import InMemoryIncidentRepository
from app.services.monitoring import MonitoringError, MonitoringService
from app.services.trip_intake import TripIntakeService


def request() -> TripImportRequest:
    return TripImportRequest(
        flights=[
            FlightImport(
                flight_number="LO351",
                provider="LOT",
                origin="WAW",
                destination="MUC",
                departure_at=datetime(2026, 8, 20, 15, tzinfo=UTC),
                arrival_at=datetime(2026, 8, 20, 18, tzinfo=UTC),
                booking_reference="LOT-ABC123",
            )
        ]
    )


async def _registered_trip() -> tuple[InMemoryIncidentRepository, MonitoringService, str]:
    repository = InMemoryIncidentRepository()
    imported = await TripIntakeService(repository).import_trip(
        request(), owner_user_id="telegram:101"
    )
    trip = await repository.get_trip(imported.trip_id)
    assert trip is not None
    service = MonitoringService(repository)
    await service.register_stored_schedule(trip, now=datetime(2026, 8, 1, tzinfo=UTC))
    return repository, service, trip.trip_id


async def test_stored_schedule_is_truthful_and_cannot_emit_external_status() -> None:
    repository, service, trip_id = await _registered_trip()
    subscriptions = await repository.list_monitoring_subscriptions(trip_id)

    assert len(subscriptions) == 1
    assert "no live source" in service.coverage_label(subscriptions[0]).lower()
    with pytest.raises(MonitoringError, match="no active status source"):
        await service.ingest_snapshot(
            ObservationSnapshot(
                subscription_id=subscriptions[0].subscription_id,
                source_id="stored-schedule-v1",
                status=ObservationStatus.DELAYED,
                scheduled_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
                observed_arrival=datetime(2026, 8, 20, 19, tzinfo=UTC),
                source_updated_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
                observed_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
                provider_event_id="test-1",
            )
        )


async def test_authorized_fixture_snapshot_emits_one_deduplicated_disruption() -> None:
    repository, service, trip_id = await _registered_trip()
    subscription = (await repository.list_monitoring_subscriptions(trip_id))[0]
    active = await service.activate_deterministic_flight_fixture(
        trip_id=trip_id,
        item_id=subscription.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )
    snapshot = ObservationSnapshot(
        subscription_id=active.subscription_id,
        source_id=active.source_id,
        status=ObservationStatus.DELAYED,
        scheduled_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
        observed_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
        source_updated_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
        observed_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
        provider_event_id="fixture-delay-105",
    )

    event = await service.ingest_snapshot(snapshot)
    duplicate = await service.ingest_snapshot(snapshot)

    assert event is not None
    assert event.trip_id == trip_id
    assert event.flight == "LO351"
    assert event.new_arrival - event.old_arrival == timedelta(minutes=105)
    assert duplicate is None


async def test_amadeus_binding_is_explicit_and_live() -> None:
    repository, service, trip_id = await _registered_trip()
    subscription = (await repository.list_monitoring_subscriptions(trip_id))[0]

    active = await service.activate_live_flight_status(
        trip_id=trip_id,
        item_id=subscription.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )

    assert active.source_id == "amadeus-flight-status-v1"
    assert service.coverage_label(active).startswith("Live status")


async def test_amadeus_snapshot_recovers_a_degraded_subscription() -> None:
    repository, service, trip_id = await _registered_trip()
    subscription = (await repository.list_monitoring_subscriptions(trip_id))[0]
    active = await service.activate_live_flight_status(
        trip_id=trip_id,
        item_id=subscription.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )
    assert await service.mark_live_status_degraded(
        subscription_id=active.subscription_id,
        now=datetime(2026, 8, 20, 15, 30, tzinfo=UTC),
    )
    degraded = await repository.get_monitoring_subscription(active.subscription_id)
    assert degraded is not None
    assert degraded.coverage.value == "MONITORING_DEGRADED"
    snapshot = ObservationSnapshot(
        subscription_id=active.subscription_id,
        source_id=active.source_id,
        status=ObservationStatus.ON_TIME,
        scheduled_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
        observed_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
        source_updated_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
        observed_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
        provider_event_id="amadeus-recovered-1",
    )

    assert await service.ingest_snapshot(snapshot) is None
    recovered = await repository.get_monitoring_subscription(active.subscription_id)
    assert recovered is not None
    assert recovered.coverage.value == "LIVE_STATUS"


async def test_snapshot_rejects_wrong_source_and_schedule() -> None:
    repository, service, trip_id = await _registered_trip()
    subscription = (await repository.list_monitoring_subscriptions(trip_id))[0]
    active = await service.activate_deterministic_flight_fixture(
        trip_id=trip_id,
        item_id=subscription.item_id,
        owner_user_id="telegram:101",
        now=datetime(2026, 8, 20, 15, tzinfo=UTC),
    )
    payload = {
        "subscription_id": active.subscription_id,
        "source_id": "untrusted-source",
        "status": "DELAYED",
        "scheduled_arrival": "2026-08-20T18:00:00Z",
        "observed_arrival": "2026-08-20T19:00:00Z",
        "source_updated_at": "2026-08-20T16:00:00Z",
        "observed_at": "2026-08-20T16:00:00Z",
        "provider_event_id": "wrong-source",
    }
    with pytest.raises(MonitoringError, match="not authorized"):
        await service.ingest_snapshot(ObservationSnapshot.model_validate(payload))

    payload["source_id"] = active.source_id
    payload["scheduled_arrival"] = "2026-08-20T17:00:00Z"
    with pytest.raises(MonitoringError, match="does not match"):
        await service.ingest_snapshot(ObservationSnapshot.model_validate(payload))
