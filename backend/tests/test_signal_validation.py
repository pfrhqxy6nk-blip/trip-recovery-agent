from datetime import UTC, datetime

import pytest
from app.models.domain import TravelItem, Trip
from app.models.enums import ItemType
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint, WatchpointKind
from app.services.signal_validation import GroundedSignalValidator, SignalRejected


def trip() -> Trip:
    return Trip(
        trip_id="trip-1",
        origin="WAW",
        destination="MUC",
        starts_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
        ends_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
        items=[
            TravelItem(
                item_id="flight-1",
                trip_id="trip-1",
                type=ItemType.FLIGHT,
                provider="LOT",
                external_id="LO351",
                start_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
            )
        ],
    )


def point() -> TripWatchpoint:
    return TripWatchpoint(
        watchpoint_id="watch:trip-1:flight-1:flight_status",
        trip_id="trip-1",
        item_id="flight-1",
        kind=WatchpointKind.FLIGHT_STATUS,
        query="LO351 flight status",
        trusted_domains=["airline.example"],
        due_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
    )


def signal(
    *, trust: SourceTrust = SourceTrust.OFFICIAL, airline_fault: bool | None = None
) -> GroundedTravelSignal:
    return GroundedTravelSignal(
        watchpoint_id=point().watchpoint_id,
        summary="Official delay",
        source_url="https://airline.example/status",
        source_title="Airline",
        trust=trust,
        observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
        affects_trip=True,
        suggested_event_type="FLIGHT_ARRIVAL_DELAY",
        airline_fault=airline_fault,
        observed_flight="LO351",
        old_arrival=datetime(2026, 8, 20, 16, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 17, 45, tzinfo=UTC),
    )


def test_only_exact_official_delay_becomes_disruption_event() -> None:
    event = GroundedSignalValidator().to_disruption(
        trip=trip(), watchpoint=point(), signal=signal()
    )

    assert event.flight == "LO351"
    assert event.new_arrival == datetime(2026, 8, 20, 17, 45, tzinfo=UTC)
    assert event.context["trust"] == "OFFICIAL"


def test_signal_carries_explicit_airline_fault_attribution() -> None:
    event = GroundedSignalValidator().to_disruption(
        trip=trip(), watchpoint=point(), signal=signal(airline_fault=True)
    )

    assert event.context["airline_fault"] is True


def test_public_or_mismatched_signal_cannot_trigger_recovery() -> None:
    with pytest.raises(SignalRejected, match="official"):
        GroundedSignalValidator().to_disruption(
            trip=trip(), watchpoint=point(), signal=signal(trust=SourceTrust.PUBLIC_SIGNAL)
        )


def test_official_label_from_untrusted_host_cannot_trigger_recovery() -> None:
    with pytest.raises(SignalRejected, match="allow-list"):
        GroundedSignalValidator().to_disruption(
            trip=trip(),
            watchpoint=point(),
            signal=signal().model_copy(
                update={"source_url": "https://lookalike-news.example/status"}
            ),
        )
    with pytest.raises(SignalRejected, match="does not match"):
        GroundedSignalValidator().to_disruption(
            trip=trip(),
            watchpoint=point(),
            signal=signal().model_copy(update={"observed_flight": "LH123"}),
        )
