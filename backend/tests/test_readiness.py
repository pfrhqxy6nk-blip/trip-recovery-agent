from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.demo_data import build_owned_demo_trip
from app.services.memory import InMemoryIncidentRepository
from app.services.readiness import ReadinessError, TripReadinessService


async def test_readiness_distinguishes_missing_documents_and_schedule_conflict() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    trip = build_owned_demo_trip(owner_user_id="telegram:101", trip_id="trip-101")
    second_flight = next(item for item in trip.items if item.item_id == "flight-lh1792")
    first_flight = next(item for item in trip.items if item.item_id == "flight-lo351")
    second_flight.start_at = first_flight.end_at + timedelta(minutes=30)
    await repository.seed_trip(trip)
    service = TripReadinessService(repository)
    await service.seed_demo_documents(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)

    report = await service.report(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)

    assert report.status == "NEEDS_ATTENTION"
    assert report.documents_missing == ("TRANSFER_VOUCHER",)
    assert {finding.code for finding in report.findings} == {
        "MISSING_DOCUMENT",
        "SCHEDULE_CONFLICT",
    }


async def test_readiness_is_owner_scoped_and_becomes_ready_with_voucher() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    trip = build_owned_demo_trip(owner_user_id="telegram:101", trip_id="trip-101")
    await repository.seed_trip(trip)
    service = TripReadinessService(repository)
    await service.seed_demo_documents(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)

    with pytest.raises(ReadinessError, match="another traveler"):
        await service.report(trip_id=trip.trip_id, owner_user_id="telegram:999", now=now)

    await service.add_demo_transfer_voucher(
        trip_id=trip.trip_id, owner_user_id="telegram:101", now=now
    )
    report = await service.report(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)
    assert report.status == "READY"
    assert report.documents_missing == ()
    assert [finding.code for finding in report.findings] == ["TIGHT_CONNECTION"]
