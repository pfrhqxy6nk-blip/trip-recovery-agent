from datetime import UTC, datetime

from app.models.domain import Trip
from app.models.enums import OnboardingStep
from app.models.monitoring import MonitoringCoverage, MonitoringSubscription
from app.models.telegram import TravelerProfile
from app.models.watch import TripWatchpoint, WatchpointKind
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_conversation import TelegramConversationService


async def active_service() -> tuple[InMemoryIncidentRepository, TelegramConversationService]:
    repository = InMemoryIncidentRepository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            onboarding_step=OnboardingStep.COMPLETE,
            active_policy_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    return repository, TelegramConversationService(repository)


async def test_conversation_explains_weather_and_monitoring_without_mutation() -> None:
    repository, service = await active_service()

    view = await service.handle(
        telegram_user_id="101", telegram_chat_id="202", text="А погоду ты отслеживаешь?"
    )

    assert "weather warnings" in view.text
    assert repository.trips == {}


async def test_conversation_shows_only_the_travelers_trip_status() -> None:
    repository, service = await active_service()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    trip = Trip(
        trip_id="trip-101",
        owner_user_id="telegram:101",
        origin="WAW",
        destination="LIS",
        starts_at=now,
        ends_at=now,
    )
    await repository.seed_trip(trip)
    await repository.put_watchpoint(
        TripWatchpoint(
            watchpoint_id="watch-101",
            trip_id=trip.trip_id,
            kind=WatchpointKind.WEATHER_IMPACT,
            query="Lisbon weather warning travel disruption",
            due_at=now,
        )
    )
    await repository.seed_trip(
        trip.model_copy(update={"trip_id": "trip-999", "owner_user_id": "telegram:999"})
    )

    view = await service.handle(
        telegram_user_id="101", telegram_chat_id="202", text="my trip status"
    )

    assert "WAW → LIS" in view.text
    assert "1 watchpoints" in view.text
    assert "trip-999" not in view.text


async def test_free_text_never_executes_a_recovery_action() -> None:
    repository, service = await active_service()

    view = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="Please rebook my flight now and charge my card",
    )

    assert "never execute a booking" in view.text
    assert repository.incidents == {}
    assert repository.actions == {}


async def test_trip_status_exposes_degraded_live_coverage() -> None:
    repository, service = await active_service()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    trip = Trip(
        trip_id="trip-101",
        owner_user_id="telegram:101",
        origin="WAW",
        destination="LIS",
        starts_at=now,
        ends_at=now,
    )
    await repository.seed_trip(trip)
    await repository.put_monitoring_subscription(
        MonitoringSubscription(
            subscription_id="monitor:trip-101:flight-1",
            trip_id=trip.trip_id,
            item_id="flight-1",
            owner_user_id="telegram:101",
            source_id="amadeus-flight-status-v1",
            coverage=MonitoringCoverage.MONITORING_DEGRADED,
            created_at=now,
            updated_at=now,
        )
    )

    view = await service.handle(
        telegram_user_id="101", telegram_chat_id="202", text="my trip status"
    )

    assert "1 checks need attention" in view.text
    assert "monitoring healthy" not in view.text
