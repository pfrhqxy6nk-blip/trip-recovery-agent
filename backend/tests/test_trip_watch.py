from datetime import UTC, datetime, timedelta

from app.models.domain import TravelItem, Trip
from app.models.enums import ItemType
from app.models.watch import WatchpointKind
from app.services.trip_watch import TripWatchPlanner


def test_watch_planner_builds_relevant_watchpoints_without_user_configuration() -> None:
    trip = Trip(
        trip_id="trip-1",
        owner_user_id="telegram:101",
        origin="WAW",
        destination="LIS",
        starts_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        ends_at=datetime(2026, 8, 23, 10, tzinfo=UTC),
        items=[
            TravelItem(
                item_id="flight-1",
                trip_id="trip-1",
                type=ItemType.FLIGHT,
                provider="LOT",
                external_id="LO351",
                origin="WAW",
                destination="MUC",
                start_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
            ),
            TravelItem(
                item_id="hotel-1",
                trip_id="trip-1",
                type=ItemType.HOTEL_ARRIVAL,
                provider="Hotel",
                location="Lisbon Riverside Hotel",
                start_at=datetime(2026, 8, 20, 18, tzinfo=UTC),
                end_at=datetime(2026, 8, 23, 10, tzinfo=UTC),
            ),
        ],
    )

    now = datetime(2026, 8, 16, 10, tzinfo=UTC)
    watchpoints = TripWatchPlanner().build(trip, now=now)

    assert {point.kind for point in watchpoints} == {
        WatchpointKind.FLIGHT_STATUS,
        WatchpointKind.AIRPORT_DISRUPTION,
        WatchpointKind.WEATHER_IMPACT,
        WatchpointKind.HOTEL_STATUS,
    }
    assert all(point.trip_id == trip.trip_id for point in watchpoints)
    assert all("  " not in point.query for point in watchpoints)
    assert all(point.due_at == now for point in watchpoints)
    assert all(point.check_interval_minutes == 360 for point in watchpoints)


def test_watch_planner_increases_frequency_near_departure() -> None:
    now = datetime(2026, 8, 20, 8, tzinfo=UTC)
    assert TripWatchPlanner._interval_minutes(now, now + timedelta(days=8)) == 720
    assert TripWatchPlanner._interval_minutes(now, now + timedelta(days=4)) == 360
    assert TripWatchPlanner._interval_minutes(now, now + timedelta(hours=8)) == 60
    assert TripWatchPlanner._interval_minutes(now, now + timedelta(hours=2)) == 30
