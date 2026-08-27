from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.enums import ActionCategory, ActionStatus, IncidentStatus
from app.models.expense import ExpenseCategory, TripExpense
from app.models.money import Money
from app.services.canonical_hash import canonical_hash
from app.services.ports import IncidentRepository


class ExpenseError(ValueError):
    pass


@dataclass(frozen=True)
class ExpenseSummary:
    trip_total: Money
    disruption_total: Money
    count: int


class TripExpenseService:
    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def record_recovery_expenses(
        self, *, incident_id: str, now: datetime
    ) -> list[TripExpense]:
        incident = await self._repository.get_incident(incident_id)
        if incident is None:
            raise ExpenseError("recovery incident does not exist")
        trip = await self._repository.get_trip(incident.trip_id)
        if trip is None or trip.owner_user_id is None:
            return []

        recorded: list[TripExpense] = []
        for action in await self._repository.list_actions(incident_id):
            if action.execution_status != ActionStatus.VERIFIED or action.cost.minor_units <= 0:
                continue
            category: ExpenseCategory = (
                "FLIGHT" if action.category == ActionCategory.FLIGHT_RECOVERY else "OTHER"
            )
            expense = TripExpense(
                expense_id=f"expense-{canonical_hash({'effect': action.effect_key})[:24]}",
                trip_id=trip.trip_id,
                owner_user_id=trip.owner_user_id,
                amount=action.cost,
                category=category,
                source="RECOVERY_ACTION",
                merchant="Demo Airline" if category == "FLIGHT" else action.provider,
                description="Verified recovery action",
                incident_id=incident_id,
                source_effect_key=action.effect_key,
                occurred_at=now,
                created_at=now,
            )
            await self._repository.save_expense_once(expense)
            stored = await self._repository.get_expense(expense.expense_id)
            if stored is not None:
                recorded.append(stored)
        return recorded

    async def record_demo_taxi(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
    ) -> TripExpense:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None or traveler.telegram_chat_id != telegram_chat_id:
            raise ExpenseError("expense belongs to another traveler")
        trip_id = f"telegram-demo-trip:{telegram_user_id}"
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id != traveler.user_id:
            raise ExpenseError("run the recovery demo before adding its taxi receipt")
        if trip.active_incident_id is None:
            raise ExpenseError("the demo trip has no recovery incident")
        incident = await self._repository.get_incident(trip.active_incident_id)
        if (
            incident is None
            or not incident.external_event_id.startswith("telegram-demo:")
            or incident.status != IncidentStatus.RECOVERED
        ):
            raise ExpenseError("finish the recovery demo before adding its taxi receipt")

        expense_id = f"expense-demo-taxi-{canonical_hash({'incident': incident.incident_id})[:20]}"
        expense = TripExpense(
            expense_id=expense_id,
            trip_id=trip_id,
            owner_user_id=traveler.user_id,
            amount=Money(currency="EUR", minor_units=2_740),
            category="TRANSPORT",
            source="RECEIPT",
            merchant="Lisbon Airport Taxi",
            description="Demo receipt linked to the flight disruption",
            incident_id=incident.incident_id,
            extraction_confidence=0.99,
            occurred_at=now,
            created_at=now,
        )
        await self._repository.save_expense_once(expense)
        stored = await self._repository.get_expense(expense.expense_id)
        if stored is None:
            raise ExpenseError("could not persist the demo expense")
        return stored

    async def summary(self, *, trip_id: str, owner_user_id: str) -> ExpenseSummary:
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise ExpenseError("trip expenses belong to another traveler")
        expenses = await self._repository.list_expenses(trip_id)
        trip_total = Money(currency="EUR", minor_units=0)
        disruption_total = Money(currency="EUR", minor_units=0)
        for expense in expenses:
            if expense.owner_user_id != owner_user_id:
                raise ExpenseError("expense ownership invariant failed")
            trip_total = trip_total.add(expense.amount)
            if expense.incident_id is not None:
                disruption_total = disruption_total.add(expense.amount)
        return ExpenseSummary(
            trip_total=trip_total,
            disruption_total=disruption_total,
            count=len(expenses),
        )
