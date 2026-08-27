from datetime import UTC, date, datetime

import pytest
from app.models.enums import OnboardingStep, TripStatus
from app.models.planning import FlexibleTravelPlanRequest, TravelPlanRequest
from app.models.telegram import TravelerProfile
from app.models.trip_intake import TripDraft
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_planning import DeterministicTripPlanner, TelegramPlanningService


async def active_repository() -> InMemoryIncidentRepository:
    repository = InMemoryIncidentRepository()
    now = datetime(2026, 8, 23, tzinfo=UTC)
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


@pytest.mark.asyncio
async def test_plan_is_persistent_and_never_presented_as_a_booking() -> None:
    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    options = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="/plan Lisbon | 2026-09-08 | 2026-09-12 | 900 | food, museums",
        now=now,
    )

    assert "Planning Lisbon" in options.text
    assert "not bookings" in options.text
    assert len(options.button_rows) == 3
    draft = await repository.get_trip_draft("101")
    assert draft is not None
    assert draft.planning_request is not None
    assert len(draft.planning_options) == 3

    selected = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="plan:select:balanced",
        now=now,
    )
    assert "selected" in selected.text
    assert "real ticket" in selected.text
    persisted = await repository.get_trip_draft("101")
    assert persisted is not None and persisted.selected_plan_id == "balanced"
    saved = await service.handle(
        telegram_user_id="101", telegram_chat_id="202", callback_data="plan:save", now=now
    )
    assert "Plan saved" in saved.text
    persisted = await repository.get_trip_draft("101")
    assert persisted is not None and persisted.planning_saved_at == now
    assert persisted.planned_trip_id is not None
    planned_trip = await repository.get_trip(persisted.planned_trip_id)
    assert planned_trip is not None and planned_trip.status == TripStatus.PLANNED
    assert all(item.status == "PLANNED" for item in planned_trip.items)


@pytest.mark.asyncio
async def test_recommendations_are_explicitly_opt_in() -> None:
    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    off = await service.handle(
        telegram_user_id="101", telegram_chat_id="202", callback_data="plan:preferences", now=now
    )
    assert "off" in off.text
    on = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="plan:recommendations_on",
        now=now,
    )
    assert "on" in on.text
    traveler = await repository.get_traveler("101")
    assert traveler is not None and traveler.recommendations_enabled is True


@pytest.mark.asyncio
async def test_natural_language_duration_and_budget_generate_flexible_options() -> None:
    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    view = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="I want to Paris for 6 nights, budget €600",
        now=now,
    )

    assert "flexible dates" in view.text
    assert "not bookings" in view.text
    draft = await repository.get_trip_draft("101")
    assert draft is not None and isinstance(draft.planning_request, FlexibleTravelPlanRequest)
    assert draft.planning_request.destination == "Paris"
    assert draft.planning_request.nights == 6
    assert draft.planning_request.budget_eur == 600
    assert draft.planning_context is None

    # Firestore stores the Pydantic payload and reconstructs it on the next process.
    restored = TripDraft.model_validate(draft.model_dump(mode="python"))
    assert isinstance(restored.planning_request, FlexibleTravelPlanRequest)
    assert restored.planning_request.nights == 6


@pytest.mark.asyncio
async def test_follow_up_completes_natural_language_brief_and_generates_options() -> None:
    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="I want to Paris for 6 nights, budget €600",
        now=now,
    )
    view = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="from Kyiv, 2026-10-10",
        now=now,
    )

    assert "Planning Paris" in view.text
    draft = await repository.get_trip_draft("101")
    assert draft is not None
    assert draft.planning_context is None
    assert draft.planning_request is not None
    assert draft.planning_request.origin == "Kyiv"
    assert isinstance(draft.planning_request, TravelPlanRequest)
    assert draft.planning_request.start_date.isoformat() == "2026-10-10"
    assert draft.planning_request.end_date.isoformat() == "2026-10-16"
    assert len(draft.planning_options) == 3


@pytest.mark.asyncio
async def test_fallback_options_are_explicit_estimates_with_queryable_sources() -> None:
    planner = DeterministicTripPlanner()
    now = datetime(2026, 8, 23, tzinfo=UTC)
    options = await planner.generate(
        request=TravelPlanRequest(
            origin="Kyiv",
            destination="Paris",
            start_date=date(2026, 10, 10),
            end_date=date(2026, 10, 16),
            budget_eur=600,
        ),
        now=now,
    )

    assert len(options) == 3
    assert all(option.availability == "ESTIMATE" for option in options)
    assert all(option.transport is not None and option.stay is not None for option in options)
    transport = options[0].transport
    stay = options[0].stay
    assert transport is not None and stay is not None
    assert transport.service == "AF 1235"
    assert transport.price_eur + stay.price_eur == 490
    assert stay.nights == 6
    assert all(
        "Kyiv" in option.source_links[0] or "Kyiv+to" in option.source_links[0]
        for option in options
    )
