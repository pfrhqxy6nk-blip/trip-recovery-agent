from datetime import UTC, datetime

from app.models.domain import Dependency, TravelItem, Trip
from app.models.enums import DependencyType, ItemType, TripStatus


def build_demo_trip() -> Trip:
    trip_id = "demo-trip-001"
    return Trip(
        trip_id=trip_id,
        status=TripStatus.HEALTHY,
        origin="WAW",
        destination="LIS",
        starts_at=datetime(2026, 8, 20, 16, 30, tzinfo=UTC),
        ends_at=datetime(2026, 8, 20, 22, 30, tzinfo=UTC),
        items=[
            TravelItem(
                item_id="flight-lo351",
                trip_id=trip_id,
                type=ItemType.FLIGHT,
                provider="LOT",
                start_at=datetime(2026, 8, 20, 16, 30, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
                origin="WAW",
                destination="MUC",
                external_id="LO351",
                flexibility="AIRLINE_CHANGE_ONLY",
            ),
            TravelItem(
                item_id="flight-lh1792",
                trip_id=trip_id,
                type=ItemType.FLIGHT,
                provider="Lufthansa",
                start_at=datetime(2026, 8, 20, 18, 55, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 21, 5, tzinfo=UTC),
                origin="MUC",
                destination="LIS",
                external_id="LH1792",
                flexibility="AIRLINE_CHANGE_ONLY",
            ),
            TravelItem(
                item_id="airport-transfer",
                trip_id=trip_id,
                type=ItemType.TRANSFER,
                provider="Demo Transfer",
                start_at=datetime(2026, 8, 20, 21, 20, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 22, 0, tzinfo=UTC),
                origin="LIS",
                destination="Lisbon city centre",
                external_id="TRANSFER-001",
                flexibility="REVERSIBLE",
            ),
            TravelItem(
                item_id="hotel-arrival",
                trip_id=trip_id,
                type=ItemType.HOTEL_ARRIVAL,
                provider="Demo Lisbon Hotel",
                start_at=datetime(2026, 8, 20, 22, 15, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 22, 30, tzinfo=UTC),
                location="Lisbon city centre",
                external_id="HOTEL-001",
                flexibility="LATE_ARRIVAL_NOTICE",
            ),
        ],
        dependencies=[
            Dependency(
                dependency_id="dep-lo351-lh1792",
                trip_id=trip_id,
                from_item_id="flight-lo351",
                to_item_id="flight-lh1792",
                type=DependencyType.CONNECTION,
                min_buffer_minutes=45,
            ),
            Dependency(
                dependency_id="dep-lh1792-transfer",
                trip_id=trip_id,
                from_item_id="flight-lh1792",
                to_item_id="airport-transfer",
                type=DependencyType.FOLLOW_ON,
                min_buffer_minutes=15,
            ),
            Dependency(
                dependency_id="dep-transfer-hotel",
                trip_id=trip_id,
                from_item_id="airport-transfer",
                to_item_id="hotel-arrival",
                type=DependencyType.FOLLOW_ON,
                min_buffer_minutes=15,
            ),
        ],
    )
