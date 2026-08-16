import asyncio

from app.config import get_settings
from app.demo_data import build_demo_trip
from app.services.firestore import FirestoreIncidentRepository


async def seed() -> None:
    settings = get_settings()
    repository = FirestoreIncidentRepository(settings.google_cloud_project)
    trip = build_demo_trip()
    await repository.seed_trip(trip)
    print(f"Seeded {trip.trip_id} with {len(trip.items)} items and {len(trip.dependencies)} edges")


if __name__ == "__main__":
    asyncio.run(seed())
