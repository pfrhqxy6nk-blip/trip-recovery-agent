from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.trip_intake import FlightImport, HotelImport, TripImportRequest
from app.services.memory import InMemoryIncidentRepository
from app.services.trip_intake import TripImportConflict, TripIntakeService
from pydantic import ValidationError


def request() -> TripImportRequest:
    return TripImportRequest(
        flights=[
            FlightImport(
                flight_number="LO351",
                provider="LOT",
                origin="WAW",
                destination="MUC",
                departure_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
                arrival_at=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
                booking_reference="LOT-ABC123",
            ),
            FlightImport(
                flight_number="LH1792",
                provider="Lufthansa",
                origin="MUC",
                destination="LIS",
                departure_at=datetime(2026, 8, 20, 18, 55, tzinfo=UTC),
                arrival_at=datetime(2026, 8, 20, 21, 5, tzinfo=UTC),
                booking_reference="LH-XYZ789",
            ),
        ],
        hotel=HotelImport(
            provider="demo-hotel",
            name="Lisbon Riverside Hotel",
            check_in_at=datetime(2026, 8, 20, 22, 0, tzinfo=UTC),
            check_out_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
            booking_reference="HOTEL-123",
        ),
        minimum_connection_minutes=45,
    )


async def test_import_creates_owned_dependency_graph_and_is_idempotent() -> None:
    repository = InMemoryIncidentRepository()
    service = TripIntakeService(repository)

    created = await service.import_trip(request(), owner_user_id="telegram:101")
    duplicate = await service.import_trip(request(), owner_user_id="telegram:101")
    trip = await repository.get_trip(created.trip_id)

    assert created.created is True
    assert duplicate.created is False
    assert duplicate.trip_id == created.trip_id
    assert trip is not None and trip.owner_user_id == "telegram:101"
    assert trip.intake_hash
    assert [item.item_id for item in trip.items] == [
        "flight-1-lo351",
        "flight-2-lh1792",
        "hotel-arrival-1",
    ]
    assert len(trip.dependencies) == 2
    assert trip.items[0].booking_reference == "LOT-ABC123"
    assert trip.items[0].external_id == "LO351"


async def test_import_supports_hotel_only_confirmation_without_synthetic_flight() -> None:
    repository = InMemoryIncidentRepository()
    service = TripIntakeService(repository)
    hotel = HotelImport(
        provider="Airbnb",
        name="Lisbon apartment",
        check_in_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
        check_out_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        booking_reference="AIR123",
    )

    result = await service.import_trip(
        TripImportRequest(flights=[], hotel=hotel),
        owner_user_id="telegram:hotel-only",
    )
    trip = await repository.get_trip(result.trip_id)

    assert trip is not None
    assert trip.origin == "STAY"
    assert trip.destination == "Lisbon apartment"
    assert [item.type.value for item in trip.items] == ["HOTEL_ARRIVAL"]
    assert trip.dependencies == []


async def test_import_requires_server_derived_telegram_owner_and_scopes_trip_identity() -> None:
    repository = InMemoryIncidentRepository()
    service = TripIntakeService(repository)

    with pytest.raises(TripImportConflict, match="derived from an authenticated Telegram identity"):
        await service.import_trip(request(), owner_user_id="user-controlled-owner")

    first = await service.import_trip(request(), owner_user_id="telegram:101")
    second = await service.import_trip(request(), owner_user_id="telegram:999")

    assert first.trip_id != second.trip_id
    assert (await repository.get_trip(first.trip_id)).owner_user_id == "telegram:101"  # type: ignore[union-attr]
    assert (await repository.get_trip(second.trip_id)).owner_user_id == "telegram:999"  # type: ignore[union-attr]


def test_import_rejects_broken_connection_and_naive_datetimes() -> None:
    payload = request().model_dump()
    payload["flights"][1]["origin"] = "FRA"
    with pytest.raises(ValidationError, match="same airport"):
        TripImportRequest.model_validate(payload)

    payload = request().model_dump()
    payload["flights"][0]["departure_at"] = datetime(2026, 8, 20, 15, 0)
    with pytest.raises(ValidationError, match="timezone"):
        TripImportRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("flight_number", "LO/351", "letters and numbers"),
        ("provider", "LOT\nAir", "control characters"),
        ("origin", "Warsaw", "three-letter IATA"),
        ("destination", "M1C", "three-letter IATA"),
        ("booking_reference", "ABC 123", "unsupported characters"),
    ],
)
def test_import_strictly_validates_untrusted_flight_fields(
    field: str, value: str, message: str
) -> None:
    payload = request().flights[0].model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        FlightImport.model_validate(payload)


def test_import_normalizes_safe_fields_and_forbids_client_owner_field() -> None:
    flight = FlightImport.model_validate(
        {
            **request().flights[0].model_dump(),
            "flight_number": " lo 351 ",
            "origin": " waw ",
            "booking_reference": " lot-abc123 ",
        }
    )
    payload = request().model_dump()
    payload["owner_user_id"] = "telegram:999"

    assert flight.flight_number == "LO351"
    assert flight.origin == "WAW"
    assert flight.booking_reference == "LOT-ABC123"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TripImportRequest.model_validate(payload)
