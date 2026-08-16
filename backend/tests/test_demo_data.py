from app.demo_data import build_demo_trip
from app.services.memory import InMemoryIncidentRepository


async def test_demo_seeder_is_idempotent() -> None:
    repository = InMemoryIncidentRepository()
    trip = build_demo_trip()

    await repository.seed_trip(trip)
    await repository.seed_trip(trip)

    stored = await repository.get_trip(trip.trip_id)
    assert stored is not None
    assert len(repository.trips) == 1
    assert len(stored.items) == 4
    assert len(stored.dependencies) == 3
    assert stored.dependencies[0].min_buffer_minutes == 45
