from datetime import UTC, date, datetime

import pytest
from app.models.enums import OnboardingStep, TripStatus
from app.models.planning import TravelPlanRequest
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
        text="I want to go to Lisbon for 4 nights, budget €900, from Kyiv, 2026-09-08",
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
    assert "Plan saved" in selected.text
    assert "actual booking" in selected.text
    assert not selected.button_rows
    persisted = await repository.get_trip_draft("101")
    assert persisted is not None and persisted.selected_plan_id == "balanced"
    assert persisted.planning_saved_at == now
    assert persisted.planned_trip_id is not None
    planned_trip = await repository.get_trip(persisted.planned_trip_id)
    assert planned_trip is not None and planned_trip.status == TripStatus.PLANNED
    assert all(item.status == "PLANNED" for item in planned_trip.items)

    repeated = await service.handle(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="plan:select:balanced",
        now=now,
    )
    assert "already saved" in repeated.text
    assert repeated.text.count("planned-trip-") == 1


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

    assert "city you will depart from" in view.text
    assert not view.button_rows
    draft = await repository.get_trip_draft("101")
    assert draft is not None and draft.planning_request is None
    assert draft.planning_context is not None
    assert draft.planning_context.destination == "Paris"
    assert draft.planning_context.nights == 6
    assert draft.planning_context.budget_eur == 600

    # Firestore stores the Pydantic payload and reconstructs it on the next process.
    restored = TripDraft.model_validate(draft.model_dump(mode="python"))
    assert restored.planning_context is not None
    assert restored.planning_context.nights == 6


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
async def test_natural_language_route_with_from_and_to_keeps_both_cities() -> None:
    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    view = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="I want to go from Kyiv to Paris for 6 nights, budget 600 EUR.",
        now=now,
    )

    assert "Planning Paris" in view.text
    draft = await repository.get_trip_draft("101")
    assert draft is not None and draft.planning_request is not None
    assert draft.planning_request.origin == "Kyiv"
    assert draft.planning_request.destination == "Paris"


@pytest.mark.asyncio
async def test_natural_language_trip_brief_does_not_absorb_month_or_previous_plan() -> None:
    """The common concierge-style request must be parsed as fresh, usable facts."""

    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    # Seed an unrelated saved brief: a new message must replace, not blend with it.
    await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="I want to go to Rome for 4 nights, budget €700, from Warsaw",
        now=now,
    )
    view = await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text=(
            "Plan a 6-night trip to Paris from Warsaw in October for €600. "
            "Show three practical choices."
        ),
        now=now,
    )

    draft = await repository.get_trip_draft("101")
    assert draft is not None and draft.planning_request is not None
    assert "Planning Paris" in view.text
    assert draft.planning_request.origin == "Warsaw"
    assert draft.planning_request.destination == "Paris"
    assert getattr(draft.planning_request, "nights", None) == 6


@pytest.mark.asyncio
async def test_natural_language_trip_brief_stops_origin_before_budget() -> None:
    repository = await active_repository()
    service = TelegramPlanningService(repository)
    now = datetime(2026, 8, 23, tzinfo=UTC)

    await service.handle_message(
        telegram_user_id="101",
        telegram_chat_id="202",
        text="Plan 5 nights in Lisbon from Warsaw for €650. I prefer a direct flight.",
        now=now,
    )

    draft = await repository.get_trip_draft("101")
    assert draft is not None and draft.planning_request is not None
    assert draft.planning_request.origin == "Warsaw"


def test_vertex_parser_accepts_a_fenced_or_prefaced_json_array() -> None:
    from app.services.telegram_planning import VertexTripPlanner

    raw = 'Here are the options:\n```json\n[{"title": "A"}]\n```'
    parsed = VertexTripPlanner._response_array(raw)

    assert parsed == [{"title": "A"}]


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
    assert transport.service == "Flight search"
    assert transport.provider == "Google Flights"
    assert transport.price_eur + stay.price_eur == 490
    assert stay.nights == 6
    assert all(
        "Kyiv" in option.source_links[0] or "Kyiv+to" in option.source_links[0]
        for option in options
    )
