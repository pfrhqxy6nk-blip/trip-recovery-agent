from __future__ import annotations

from datetime import datetime, timedelta

from app.models.enums import IncidentStatus, TripStatus
from app.models.finance import OpenFinancialItem, TripClosureReport
from app.models.money import Money
from app.services.ports import IncidentRepository


class TripClosureError(ValueError):
    pass


class TripClosureService:
    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def report(self, *, trip_id: str, owner_user_id: str, now: datetime) -> TripClosureReport:
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise TripClosureError("trip closure belongs to another traveler")
        items = await self._repository.list_financial_items(trip_id)
        if any(item.owner_user_id != owner_user_id for item in items):
            raise TripClosureError("financial item ownership invariant failed")
        open_items = [item for item in items if item.status != "SETTLED"]
        blockers = [
            f"{item.kind.lower()} from {item.provider} is still {item.status.lower()}"
            for item in open_items
        ]
        if trip.active_incident_id is not None:
            incident = await self._repository.get_incident(trip.active_incident_id)
            if incident is not None and incident.status not in {
                IncidentStatus.RECOVERED,
                IncidentStatus.CANCELLED,
            }:
                blockers.append(f"recovery incident is still {incident.status.value.lower()}")
        if trip.status == TripStatus.CLOSED:
            status = "CLOSED"
        else:
            status = "BLOCKED" if blockers else "CAN_CLOSE"
        return TripClosureReport(
            trip_id=trip_id,
            status=status,
            open_financial_item_ids=tuple(item.financial_item_id for item in open_items),
            blockers=tuple(blockers),
            generated_at=now,
        )

    async def close_trip(
        self, *, trip_id: str, owner_user_id: str, now: datetime
    ) -> TripClosureReport:
        report = await self.report(trip_id=trip_id, owner_user_id=owner_user_id, now=now)
        if report.status == "BLOCKED":
            raise TripClosureError("trip cannot close while required items remain open")
        if report.status != "CLOSED":
            changed = await self._repository.set_trip_status(
                trip_id=trip_id,
                owner_user_id=owner_user_id,
                status=TripStatus.CLOSED,
                updated_at=now,
            )
            if not changed:
                raise TripClosureError("trip status changed before closure")
        return await self.report(trip_id=trip_id, owner_user_id=owner_user_id, now=now)

    async def seed_demo_financial_items(
        self, *, trip_id: str, owner_user_id: str, now: datetime
    ) -> None:
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise TripClosureError("trip finances belong to another traveler")
        for item in (
            OpenFinancialItem(
                financial_item_id=f"{trip_id}:hotel-deposit",
                trip_id=trip_id,
                owner_user_id=owner_user_id,
                kind="DEPOSIT",
                provider="Lisbon Demo Hotel",
                expected_amount=Money(currency="EUR", minor_units=15_000),
                due_at=now + timedelta(days=7),
                created_at=now,
                updated_at=now,
            ),
            OpenFinancialItem(
                financial_item_id=f"{trip_id}:airline-refund",
                trip_id=trip_id,
                owner_user_id=owner_user_id,
                kind="REFUND",
                provider="Demo Airline",
                expected_amount=Money(currency="EUR", minor_units=7_000),
                due_at=now + timedelta(days=14),
                created_at=now,
                updated_at=now,
            ),
        ):
            await self._repository.save_financial_item_once(item)

    async def settle_demo_financial_items(
        self, *, trip_id: str, owner_user_id: str, now: datetime
    ) -> None:
        for item in await self._repository.list_financial_items(trip_id):
            if item.owner_user_id != owner_user_id:
                raise TripClosureError("financial item belongs to another traveler")
            if item.status != "SETTLED":
                settled = await self._repository.settle_financial_item(
                    financial_item_id=item.financial_item_id,
                    owner_user_id=owner_user_id,
                    actual_amount=item.expected_amount,
                    settled_at=now,
                )
                if settled is None:
                    raise TripClosureError("could not settle demo financial item")
