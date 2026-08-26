from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.demo_data import build_owned_demo_trip
from app.models.enums import OnboardingStep
from app.models.telegram import TravelerProfile
from app.services.expenses import ExpenseError, TripExpenseService
from app.services.memory import InMemoryIncidentRepository


async def test_expense_summary_is_owner_scoped() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            onboarding_step=OnboardingStep.COMPLETE,
            created_at=now,
            updated_at=now,
        )
    )
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id="telegram:101", trip_id="telegram-demo-trip:101")
    )
    service = TripExpenseService(repository)

    with pytest.raises(ExpenseError, match="another traveler"):
        await service.summary(trip_id="telegram-demo-trip:101", owner_user_id="telegram:999")


async def test_save_expense_once_rejects_same_id_with_changed_money() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    from app.models.expense import TripExpense
    from app.models.money import Money

    original = TripExpense(
        expense_id="expense-001",
        trip_id="trip-001",
        owner_user_id="telegram:101",
        amount=Money(currency="EUR", minor_units=1_000),
        category="TRANSPORT",
        source="TELEGRAM_TEXT",
        merchant="Metro",
        occurred_at=now,
        created_at=now,
    )

    assert await repository.save_expense_once(original) is True
    assert await repository.save_expense_once(original) is True
    assert (
        await repository.save_expense_once(
            original.model_copy(update={"amount": Money(currency="EUR", minor_units=1_100)})
        )
        is False
    )
    assert len(repository.expenses) == 1
