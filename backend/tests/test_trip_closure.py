from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.demo_data import build_owned_demo_trip
from app.models.money import Money
from app.services.memory import InMemoryIncidentRepository
from app.services.trip_closure import TripClosureError, TripClosureService


async def test_trip_cannot_close_until_financial_items_settle_exactly() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    trip = build_owned_demo_trip(owner_user_id="telegram:101", trip_id="trip-101")
    await repository.seed_trip(trip)
    service = TripClosureService(repository)
    await service.seed_demo_financial_items(
        trip_id=trip.trip_id, owner_user_id="telegram:101", now=now
    )

    report = await service.report(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)
    assert report.status == "BLOCKED"
    assert len(report.open_financial_item_ids) == 2
    with pytest.raises(TripClosureError, match="cannot close"):
        await service.close_trip(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)

    deposit = next(item for item in repository.financial_items.values() if item.kind == "DEPOSIT")
    mismatched = await repository.settle_financial_item(
        financial_item_id=deposit.financial_item_id,
        owner_user_id="telegram:101",
        actual_amount=Money(currency="EUR", minor_units=14_900),
        settled_at=now,
    )
    assert mismatched is None
    await service.settle_demo_financial_items(
        trip_id=trip.trip_id, owner_user_id="telegram:101", now=now
    )
    closed = await service.close_trip(trip_id=trip.trip_id, owner_user_id="telegram:101", now=now)
    assert closed.status == "CLOSED"


async def test_trip_closure_is_owner_scoped() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    trip = build_owned_demo_trip(owner_user_id="telegram:101", trip_id="trip-101")
    await repository.seed_trip(trip)
    service = TripClosureService(repository)

    with pytest.raises(TripClosureError, match="another traveler"):
        await service.report(trip_id=trip.trip_id, owner_user_id="telegram:999", now=now)
