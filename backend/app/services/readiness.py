from __future__ import annotations

from datetime import datetime

from app.models.enums import DependencyType, ItemType
from app.models.readiness import (
    DocumentKind,
    ReadinessFinding,
    TripDocument,
    TripReadinessReport,
)
from app.services.ports import IncidentRepository


class ReadinessError(ValueError):
    pass


class TripReadinessService:
    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def report(
        self, *, trip_id: str, owner_user_id: str, now: datetime
    ) -> TripReadinessReport:
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise ReadinessError("trip readiness belongs to another traveler")
        documents = await self._repository.list_trip_documents(trip_id)
        if any(document.owner_user_id != owner_user_id for document in documents):
            raise ReadinessError("document ownership invariant failed")

        required: set[DocumentKind] = set()
        if any(item.type == ItemType.FLIGHT for item in trip.items):
            required.add("FLIGHT_TICKET")
        if any(item.type == ItemType.HOTEL_ARRIVAL for item in trip.items):
            required.add("HOTEL_CONFIRMATION")
        if any(item.type == ItemType.TRANSFER for item in trip.items):
            required.add("TRANSFER_VOUCHER")
        present = {document.kind for document in documents}
        missing = required - present
        findings = [
            ReadinessFinding(
                code="MISSING_DOCUMENT",
                severity="ATTENTION",
                summary=f"{self._document_label(kind)} is not available yet.",
            )
            for kind in sorted(missing)
        ]

        items = {item.item_id: item for item in trip.items}
        for dependency in trip.dependencies:
            if dependency.type != DependencyType.CONNECTION:
                continue
            previous = items.get(dependency.from_item_id)
            following = items.get(dependency.to_item_id)
            if previous is None or following is None:
                continue
            available = int((following.start_at - previous.end_at).total_seconds() // 60)
            if available < dependency.min_buffer_minutes:
                findings.append(
                    ReadinessFinding(
                        code="SCHEDULE_CONFLICT",
                        severity="ATTENTION",
                        summary=(
                            f"Connection has {available} minutes; "
                            f"{dependency.min_buffer_minutes} are required."
                        ),
                        item_id=following.item_id,
                    )
                )
            elif available < dependency.min_buffer_minutes + 30:
                findings.append(
                    ReadinessFinding(
                        code="TIGHT_CONNECTION",
                        severity="INFO",
                        summary=(
                            f"Munich connection is tight: {available} minutes with a "
                            f"{dependency.min_buffer_minutes}-minute minimum."
                        ),
                        item_id=following.item_id,
                    )
                )

        needs_attention = any(finding.severity == "ATTENTION" for finding in findings)
        return TripReadinessReport(
            trip_id=trip_id,
            status="NEEDS_ATTENTION" if needs_attention else "READY",
            documents_present=tuple(sorted(present)),
            documents_missing=tuple(sorted(missing)),
            findings=tuple(findings),
            generated_at=now,
        )

    async def seed_demo_documents(self, *, trip_id: str, owner_user_id: str, now: datetime) -> None:
        for document in (
            TripDocument(
                document_id=f"{trip_id}:flight-ticket",
                trip_id=trip_id,
                owner_user_id=owner_user_id,
                kind="FLIGHT_TICKET",
                linked_item_id="flight-lo351",
                display_name="Warsaw–Lisbon flight ticket",
                source="DEMO",
                created_at=now,
            ),
            TripDocument(
                document_id=f"{trip_id}:hotel-confirmation",
                trip_id=trip_id,
                owner_user_id=owner_user_id,
                kind="HOTEL_CONFIRMATION",
                linked_item_id="hotel-arrival",
                display_name="Lisbon hotel confirmation",
                source="DEMO",
                created_at=now,
            ),
        ):
            await self._repository.save_trip_document_once(document)

    async def add_demo_transfer_voucher(
        self, *, trip_id: str, owner_user_id: str, now: datetime
    ) -> None:
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise ReadinessError("trip documents belong to another traveler")
        await self._repository.save_trip_document_once(
            TripDocument(
                document_id=f"{trip_id}:transfer-voucher",
                trip_id=trip_id,
                owner_user_id=owner_user_id,
                kind="TRANSFER_VOUCHER",
                linked_item_id="airport-transfer",
                display_name="Lisbon airport transfer voucher",
                source="DEMO",
                created_at=now,
            )
        )

    @staticmethod
    def _document_label(kind: DocumentKind) -> str:
        return {
            "FLIGHT_TICKET": "Flight ticket",
            "BOARDING_PASS": "Boarding pass",
            "HOTEL_CONFIRMATION": "Hotel confirmation",
            "TRANSFER_VOUCHER": "Transfer voucher",
            "INSURANCE": "Insurance document",
            "OTHER": "Required document",
        }[kind]
