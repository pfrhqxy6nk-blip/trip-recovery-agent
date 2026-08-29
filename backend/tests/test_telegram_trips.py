from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.ai_connection import AiConnection, AiConnectionStatus
from app.models.enums import OnboardingStep
from app.models.monitoring import MonitoringCoverage, MonitoringSubscription
from app.models.telegram import TravelerProfile
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_planning import TelegramPlanningService
from app.services.telegram_trips import TelegramTripError, TelegramTripService


async def active_repository() -> InMemoryIncidentRepository:
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
    return repository


async def test_pilot_trip_is_owned_isolated_and_idempotent() -> None:
    repository = await active_repository()
    service = TelegramTripService(repository, pilot_enabled=True)
    now = datetime(2026, 8, 17, tzinfo=UTC)

    first = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:add_pilot",
        now=now,
    )
    duplicate = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:add_pilot",
        now=now,
    )
    trip = await repository.get_trip("pilot-trip:101")

    assert first.text == duplicate.text
    assert trip is not None and trip.owner_user_id == "telegram:101"
    assert all(item.trip_id == trip.trip_id for item in trip.items)
    assert all(dependency.trip_id == trip.trip_id for dependency in trip.dependencies)


def test_stored_monitoring_subscription_round_trips_with_no_observation_yet() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    subscription = MonitoringSubscription(
        subscription_id="monitor:trip:flight",
        trip_id="trip",
        item_id="flight",
        owner_user_id="telegram:101",
        source_id="stored-schedule-v1",
        coverage=MonitoringCoverage.SCHEDULE_STORED,
        created_at=now,
        updated_at=now,
    )

    restored = MonitoringSubscription.model_validate(subscription.model_dump(mode="json"))

    assert restored.source_updated_at is None
    assert restored.last_checked_at is None


async def test_pilot_trip_requires_active_owner_and_feature_flag() -> None:
    repository = await active_repository()
    disabled = TelegramTripService(repository, pilot_enabled=False)
    enabled = TelegramTripService(repository, pilot_enabled=True)
    now = datetime(2026, 8, 17, tzinfo=UTC)

    with pytest.raises(TelegramTripError, match="unavailable"):
        await disabled.handle(
            telegram_user_id="101",
            telegram_chat_id="202",
            callback_data="trip:add_pilot",
            now=now,
        )
    with pytest.raises(TelegramTripError, match="finish onboarding"):
        await enabled.handle(
            telegram_user_id="999",
            telegram_chat_id="202",
            callback_data="trip:add_pilot",
            now=now,
        )


async def test_manual_draft_flight_hotel_and_save_creates_owned_trip() -> None:
    repository = await active_repository()
    service = TelegramTripService(repository, pilot_enabled=False)
    now = datetime(2026, 8, 17, tzinfo=UTC)

    draft = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:manual:start",
        now=now,
    )
    flight = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="/flight LO351 | LOT | WAW | MUC | 2026-08-20T15:00+02:00 | 2026-08-20T18:00+02:00",
        now=now,
    )
    hotel = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text=(
            "/hotel Booking.com | Hotel Bayerischer Hof | 2026-08-20T19:00+02:00 | "
            "2026-08-23T10:00+02:00"
        ),
        now=now,
    )
    saved = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:manual:save",
        now=now,
    )

    assert "No flights yet" in draft.text
    assert "LO351: WAW → MUC" in flight.text
    assert "Hotel: Hotel Bayerischer Hof" in hotel.text
    assert "Trip protected." in saved.text
    assert "Gemini key" in saved.text
    assert not saved.inline_keyboard()
    assert await repository.get_trip_draft("101") is None
    assert len(repository.trips) == 1
    trip = next(iter(repository.trips.values()))
    assert trip.owner_user_id == "telegram:101"
    assert trip.intake_hash
    assert [item.item_id for item in trip.items] == ["flight-1-lo351", "hotel-arrival-1"]
    assert len(await repository.list_watchpoints(trip.trip_id)) == 4


async def test_saved_trip_shows_personal_watch_as_enabled_when_gemini_is_connected() -> None:
    repository = await active_repository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    await repository.save_ai_connection(
        AiConnection(
            telegram_user_id="101",
            status=AiConnectionStatus.CONNECTED,
            secret_resource_name="projects/test/secrets/user/versions/1",
            created_at=now,
        )
    )
    service = TelegramTripService(repository, pilot_enabled=False)
    await service.handle(
        telegram_user_id="101", telegram_chat_id="202", callback_data="trip:manual:start", now=now
    )
    await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="/flight LO351 | LOT | WAW | MUC | 2026-08-20T15:00+02:00 | 2026-08-20T18:00+02:00",
        now=now,
    )

    saved = await service.handle(
        telegram_user_id="101", telegram_chat_id="202", callback_data="trip:manual:save", now=now
    )

    assert "Personal Search Watch is enabled" in saved.text
    assert not saved.inline_keyboard()


async def test_natural_language_trip_intake() -> None:
    repository = await active_repository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = TelegramTripService(repository, pilot_enabled=False)

    view = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text=(
            "Flight booking confirmation: LOT Polish Airlines flight LO351 from WAW to MUC "
            "connecting to flight TP123 from MUC to LIS. PNR: ABC999. Hotel: Marriott Lisbon."
        ),
        now=now,
    )

    assert "Your private itinerary draft" in view.text
    assert "LO351: WAW → MUC" in view.text
    assert "TP123: MUC → LIS" in view.text
    assert "Hotel: Marriott Lisbon" in view.text

    saved = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:manual:save",
        now=now,
    )
    assert "Trip protected." in saved.text
    assert len(repository.trips) == 1
    trip = next(iter(repository.trips.values()))
    assert len(trip.items) == 3  # 2 flights + 1 hotel


async def test_trip_menu_explains_forwarded_multimodal_sources() -> None:
    repository = await active_repository()
    service = TelegramTripService(repository, pilot_enabled=False)
    view = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:menu",
        now=datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert "PDF ticket" in view.text
    assert "Apple Wallet .pkpass" in view.text
    assert "never send booking documents" not in view.text
    assert view.inline_keyboard()[0][0].callback_data == "trip:forward_help"


def test_forwarded_booking_text_is_distinguished_from_chat() -> None:
    assert TelegramTripService.looks_like_itinerary(
        "Booking confirmation: LOT flight LO351, WAW -> MUC, PNR ABC123"
    )
    assert not TelegramTripService.looks_like_itinerary("What do you monitor on my trip?")


@pytest.mark.asyncio
async def test_selected_plan_stays_attached_until_real_booking_is_saved() -> None:
    repository = await active_repository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    planning = TelegramPlanningService(repository)
    await planning.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="I want to go to Lisbon for 4 nights, budget €900, from Kyiv, 2026-09-08",
        now=now,
    )
    await planning.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="plan:select:balanced",
        now=now,
    )

    trips = TelegramTripService(repository, pilot_enabled=False)
    draft_view = await trips.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text=("Flight booking confirmation: LO351 from WAW to MUC. Flight TP123 from MUC to LIS."),
        now=now,
    )

    assert "Planning target: Lisbon · Balanced route" in draft_view.text
    draft = await repository.get_trip_draft("101")
    assert draft is not None and draft.selected_plan_id == "balanced"


async def test_media_trip_intake() -> None:
    repository = await active_repository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = TelegramTripService(repository, pilot_enabled=False)

    view = await service.handle_media_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        media_bytes=b"%PDF-1.4 simulated boarding pass",
        mime_type="application/pdf",
        caption=(
            "LH400 from FRA to JFK on August 20; "
            "2026-08-20T16:00:00+00:00 2026-08-20T21:00:00+00:00"
        ),
        now=now,
    )

    assert "Your private itinerary draft" in view.text
    assert "LH400" in view.text
    assert "Evidence: 1 forwarded file" in view.text


async def test_hotel_only_media_trip_can_be_saved_and_monitored() -> None:
    repository = await active_repository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = TelegramTripService(repository, pilot_enabled=False)

    view = await service.handle_media_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        media_bytes=b"opaque screenshot bytes",
        mime_type="image/png",
        caption=(
            "Airbnb reservation Lisbon apartment; "
            "2026-08-20T15:00:00+00:00 2026-08-23T10:00:00+00:00"
        ),
        now=now,
    )
    assert "Lisbon apartment" in view.text

    saved = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="trip:manual:save",
        now=now,
    )
    assert "Trip protected." in saved.text
    trip = next(iter(repository.trips.values()))
    assert [item.type.value for item in trip.items] == ["HOTEL_ARRIVAL"]
