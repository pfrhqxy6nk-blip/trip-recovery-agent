from __future__ import annotations

from datetime import UTC, datetime

from app.demo_data import build_owned_demo_trip
from app.models.enums import OnboardingStep, PolicyMode
from app.models.money import Money
from app.models.telegram import TravelerProfile
from app.services.memory import InMemoryIncidentRepository


def _traveler(telegram_id: str, owner_id: str) -> TravelerProfile:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    return TravelerProfile(
        user_id=owner_id,
        telegram_user_id=telegram_id,
        telegram_chat_id=f"chat-{telegram_id}",
        onboarding_step=OnboardingStep.COMPLETE,
        calendar_mode=PolicyMode.AUTO,
        service_message_mode=PolicyMode.AUTO,
        reversible_change_mode=PolicyMode.AUTO,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        active_policy_version=1,
        created_at=now,
        updated_at=now,
    )


async def test_delete_traveler_data_is_owner_scoped_and_returns_credentials() -> None:
    repository = InMemoryIncidentRepository()
    await repository.save_traveler(_traveler("101", "telegram:101"))
    await repository.save_traveler(_traveler("202", "telegram:202"))
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id="telegram:101", trip_id="trip-101")
    )
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id="telegram:202", trip_id="trip-202")
    )

    deleted = await repository.delete_traveler_data("101")

    assert deleted == []
    assert await repository.get_trip("trip-101") is None
    assert await repository.get_trip("trip-202") is not None
    assert "101" not in repository.travelers
    assert "202" in repository.travelers


async def test_delete_unknown_or_repeated_traveler_is_idempotent() -> None:
    repository = InMemoryIncidentRepository()
    assert await repository.delete_traveler_data("missing") == []
    await repository.save_traveler(_traveler("101", "telegram:101"))
    assert await repository.delete_traveler_data("101") == []
    assert await repository.delete_traveler_data("101") == []
