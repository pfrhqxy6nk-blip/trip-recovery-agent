from __future__ import annotations

from app.models.domain import Dependency, TravelItem, Trip
from app.models.enums import DependencyType, ItemType
from app.models.trip_intake import TripImportRequest, TripImportResult
from app.services.canonical_hash import canonical_hash
from app.services.ports import IncidentRepository, TripCreateConflict


class TripImportConflict(ValueError):
    """The requested trip ID is already bound to different data or another traveler."""


class TripIntakeService:
    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def import_trip(
        self, request: TripImportRequest, *, owner_user_id: str
    ) -> TripImportResult:
        if not owner_user_id.startswith("telegram:"):
            raise TripImportConflict(
                "trip owner must be derived from an authenticated Telegram identity"
            )
        trip = self.build_trip(request, owner_user_id=owner_user_id)
        try:
            created = await self._repository.create_trip_once(trip)
        except TripCreateConflict as exc:
            raise TripImportConflict(
                "trip ID is already used for different itinerary data"
            ) from exc
        stored = await self._repository.get_trip(trip.trip_id)
        if stored is None or stored.owner_user_id != owner_user_id:
            raise TripImportConflict("trip could not be persisted for this traveler")
        return self._result(stored, created=created)

    @staticmethod
    def build_trip(request: TripImportRequest, *, owner_user_id: str) -> Trip:
        trip_id = f"trip-{canonical_hash({'owner': owner_user_id, 'intake': request})[:24]}"
        intake_hash = canonical_hash({"owner": owner_user_id, "intake": request})
        items: list[TravelItem] = []
        dependencies: list[Dependency] = []
        flight_item_ids: list[str] = []

        for index, flight in enumerate(request.flights, start=1):
            item_id = f"flight-{index}-{flight.flight_number.lower()}"
            flight_item_ids.append(item_id)
            items.append(
                TravelItem(
                    item_id=item_id,
                    trip_id=trip_id,
                    type=ItemType.FLIGHT,
                    provider=flight.provider,
                    start_at=flight.departure_at,
                    end_at=flight.arrival_at,
                    origin=flight.origin.upper(),
                    destination=flight.destination.upper(),
                    departure_terminal=flight.departure_terminal,
                    arrival_terminal=flight.arrival_terminal,
                    external_id=flight.flight_number.upper(),
                    booking_reference=flight.booking_reference,
                    scheduled_local_date=flight.departure_at.date(),
                )
            )
            if index > 1:
                dependencies.append(
                    Dependency(
                        dependency_id=f"connection-{index - 1}-{index}",
                        trip_id=trip_id,
                        from_item_id=flight_item_ids[index - 2],
                        to_item_id=item_id,
                        type=DependencyType.CONNECTION,
                        min_buffer_minutes=request.minimum_connection_minutes,
                    )
                )

        if request.hotel is not None:
            hotel_id = "hotel-arrival-1"
            items.append(
                TravelItem(
                    item_id=hotel_id,
                    trip_id=trip_id,
                    type=ItemType.HOTEL_ARRIVAL,
                    provider=request.hotel.provider,
                    start_at=request.hotel.check_in_at,
                    end_at=request.hotel.check_out_at,
                    location=request.hotel.name,
                    external_id=request.hotel.booking_reference,
                    contact_email=request.hotel.contact_email,
                )
            )
            if flight_item_ids:
                dependencies.append(
                    Dependency(
                        dependency_id="final-flight-to-hotel",
                        trip_id=trip_id,
                        from_item_id=flight_item_ids[-1],
                        to_item_id=hotel_id,
                        type=DependencyType.FOLLOW_ON,
                    )
                )

        if request.flights:
            first = request.flights[0]
            origin = first.origin.upper()
            destination = request.flights[-1].destination.upper()
            starts_at = first.departure_at
            created_at = first.departure_at
            last_end = (
                request.hotel.check_out_at
                if request.hotel is not None
                else request.flights[-1].arrival_at
            )
        else:
            assert request.hotel is not None
            origin = "STAY"
            destination = request.hotel.name
            starts_at = request.hotel.check_in_at
            created_at = request.hotel.check_in_at
            last_end = request.hotel.check_out_at
        return Trip(
            trip_id=trip_id,
            owner_user_id=owner_user_id,
            intake_hash=intake_hash,
            origin=origin,
            destination=destination,
            starts_at=starts_at,
            ends_at=last_end,
            items=items,
            dependencies=dependencies,
            created_at=created_at,
            updated_at=created_at,
        )

    @staticmethod
    def _result(trip: Trip, *, created: bool) -> TripImportResult:
        return TripImportResult(
            trip_id=trip.trip_id,
            created=created,
            item_count=len(trip.items),
            dependency_count=len(trip.dependencies),
        )
