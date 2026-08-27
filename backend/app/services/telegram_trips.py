from __future__ import annotations

import hashlib
import re
from datetime import datetime

from pydantic import ValidationError

from app.agents.itinerary_extractor import ItineraryExtractor
from app.demo_data import build_owned_demo_trip
from app.models.ai_connection import AiConnectionStatus
from app.models.enums import OnboardingStep, TripStatus
from app.models.readiness import TripDocument
from app.models.telegram import TelegramButton, TelegramView, TravelerProfile
from app.models.trip_intake import (
    FlightImport,
    HotelImport,
    TripDraft,
    TripImportRequest,
    TripSourceFile,
)
from app.services.monitoring import MonitoringService
from app.services.ports import IncidentRepository
from app.services.trip_intake import TripImportConflict, TripIntakeService
from app.services.trip_watch import TripWatchPlanner


class TelegramTripError(ValueError):
    pass


class TelegramTripService:
    """Explicit, multimodal and natural language itinerary intake for Telegram travelers."""

    def __init__(
        self,
        repository: IncidentRepository,
        *,
        pilot_enabled: bool,
        judge_mode: bool = False,
        amadeus_enabled: bool = False,
        extractor: ItineraryExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._pilot_enabled = pilot_enabled
        self._judge_mode = judge_mode
        self._amadeus_enabled = amadeus_enabled
        self._extractor = extractor or ItineraryExtractor()
        self._intake = TripIntakeService(repository)
        self._monitoring = MonitoringService(repository)
        self._watch_planner = TripWatchPlanner()

    @staticmethod
    def looks_like_itinerary(text: str) -> bool:
        """Detect forwarded booking text without hijacking normal chat."""
        normalized = text.strip().lower()
        if not normalized:
            return False
        if normalized.startswith(("/flight ", "/hotel ", "/import ", "flight ", "hotel ")):
            return True
        booking_words = (
            "booking confirmation",
            "reservation",
            "boarding pass",
            "e-ticket",
            "itinerary",
            "pnr",
            "booking reference",
            "airbnb",
            "check-in",
            "check in",
        )
        if any(word in normalized for word in booking_words):
            return True
        has_flight_code = re.search(r"\b[A-Z0-9]{2}\s?\d{1,4}\b", text, flags=re.IGNORECASE)
        has_airport_route = re.search(
            r"\b[A-Z]{3}\b\s*(?:→|->|to|–|-)\s*\b[A-Z]{3}\b", text, flags=re.IGNORECASE
        )
        return has_flight_code is not None and has_airport_route is not None

    async def handle(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        callback_data: str,
        now: datetime,
    ) -> TelegramView:
        traveler = await self._active_traveler(telegram_user_id, telegram_chat_id)
        if callback_data == "trip:menu":
            return await self._menu_view(telegram_user_id)
        if callback_data == "trip:forward_help":
            return self._forward_help_view()
        if callback_data == "trip:manual:start":
            return await self._start_draft(
                traveler.user_id, telegram_user_id, telegram_chat_id, now
            )
        if callback_data == "trip:manual:add_flight":
            return self._flight_instruction()
        if callback_data == "trip:manual:add_hotel":
            return self._hotel_instruction()
        if callback_data == "trip:manual:save":
            return await self._save_draft(traveler.user_id, telegram_user_id, telegram_chat_id, now)
        if callback_data == "trip:manual:cancel":
            return await self._cancel_draft(telegram_user_id, telegram_chat_id)
        if callback_data == "trip:add_pilot" and self._pilot_enabled:
            return await self._add_pilot(traveler.user_id, telegram_user_id)
        raise TelegramTripError("this trip action is unavailable")

    async def handle_message(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        text: str,
        now: datetime,
    ) -> TelegramView:
        traveler = await self._active_traveler(telegram_user_id, telegram_chat_id)
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if draft is None or draft.telegram_chat_id != telegram_chat_id:
            draft = TripDraft(
                draft_id=f"draft:{telegram_user_id}",
                owner_user_id=traveler.user_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                created_at=now,
                updated_at=now,
            )
            saved_draft = await self._repository.save_trip_draft(draft=draft, expected_version=None)
            if saved_draft is None:
                raise TelegramTripError("could not start your itinerary draft")
            draft = saved_draft

        try:
            if text.startswith("/flight "):
                flight = self._parse_flight(text)
                new_flights = [*draft.flights, flight]
                new_hotel = draft.hotel
            elif text.startswith("/hotel "):
                hotel = self._parse_hotel(text)
                new_flights = draft.flights
                new_hotel = hotel
            else:
                # Natural language or forwarded email text parsing
                extracted = await self._extractor.extract_from_text(text, reference_time=now)
                new_flights = extracted.flights if extracted.flights else draft.flights
                new_hotel = extracted.hotel if extracted.hotel else draft.hotel

            saved = await self._repository.save_trip_draft(
                draft=draft.model_copy(
                    update={
                        "flights": new_flights,
                        "hotel": new_hotel,
                        "updated_at": now,
                    }
                ),
                expected_version=draft.version,
            )
        except ValidationError as exc:
            raise TelegramTripError(
                "those details are not valid; check the format and time zone"
            ) from exc
        if saved is None:
            raise TelegramTripError("your itinerary changed in another message; open it again")
        if saved.owner_user_id != traveler.user_id:
            raise TelegramTripError("this itinerary belongs to another traveler")
        return self._draft_view(saved)

    async def handle_media_message(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        media_bytes: bytes,
        mime_type: str,
        caption: str = "",
        source_id: str | None = None,
        source_name: str | None = None,
        now: datetime,
    ) -> TelegramView:
        if not media_bytes:
            raise TelegramTripError("the received document was empty; please send it again")
        traveler = await self._active_traveler(telegram_user_id, telegram_chat_id)
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if draft is None or draft.telegram_chat_id != telegram_chat_id:
            draft = TripDraft(
                draft_id=f"draft:{telegram_user_id}",
                owner_user_id=traveler.user_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                created_at=now,
                updated_at=now,
            )
            saved_draft = await self._repository.save_trip_draft(draft=draft, expected_version=None)
            if saved_draft is None:
                raise TelegramTripError("could not start your itinerary draft")
            draft = saved_draft

        try:
            extracted = await self._extractor.extract_from_media(
                media_bytes=media_bytes,
                mime_type=mime_type,
                caption=caption,
                reference_time=now,
            )
            saved = await self._repository.save_trip_draft(
                draft=draft.model_copy(
                    update={
                        "flights": extracted.flights if extracted.flights else draft.flights,
                        "hotel": extracted.hotel or draft.hotel,
                        "updated_at": now,
                    }
                ),
                expected_version=draft.version,
            )
        except (ValidationError, ValueError) as exc:
            raise TelegramTripError(
                "could not read a flight or hotel from this document; add a caption with "
                "the flight number and route or connect Gemini for vision extraction"
            ) from exc

        if saved is None:
            raise TelegramTripError("your itinerary changed in another message; please try again")
        source = self._source_file(
            media_bytes=media_bytes,
            mime_type=mime_type,
            caption=caption,
            source_id=source_id,
            source_name=source_name,
            received_at=now,
        )
        source_files = [
            existing for existing in saved.source_files if existing.source_id != source.source_id
        ]
        source_files.append(source)
        saved_with_source = saved.model_copy(
            update={"source_files": source_files[-8:], "updated_at": now}
        )
        saved = (
            await self._repository.save_trip_draft(
                draft=saved_with_source, expected_version=saved.version
            )
            or saved
        )
        return self._draft_view(saved)

    async def _active_traveler(
        self, telegram_user_id: str, telegram_chat_id: str
    ) -> TravelerProfile:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
            or traveler.onboarding_step != OnboardingStep.COMPLETE
        ):
            raise TelegramTripError("finish onboarding before adding a trip")
        return traveler

    async def _menu_view(self, telegram_user_id: str) -> TelegramView:
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if draft is not None:
            return self._draft_view(draft)
        buttons = [TelegramButton(text="Add my itinerary", callback_data="trip:manual:start")]
        return TelegramView(
            text=(
                "Add a trip you want me to protect.\n\n"
                "Simply forward a PDF ticket, Booking/Airbnb confirmation, Apple Wallet "
                ".pkpass, screenshot, or booking email. I will extract the flight, PNR, "
                "times, terminals, connections and hotel, then show you the draft before "
                "monitoring starts.\n\n"
                "Only send travel documents — never payment data or passport information."
            ),
            button_rows=[
                [
                    TelegramButton(
                        text="How to forward a booking", callback_data="trip:forward_help"
                    )
                ],
                buttons,
            ],
        )

    @staticmethod
    def _forward_help_view() -> TelegramView:
        return TelegramView(
            text=(
                "Forward anything that already contains your trip:\n\n"
                "• PDF ticket or Booking/Airbnb email\n"
                "• Apple Wallet .pkpass\n"
                "• screenshot of a booking or boarding pass\n\n"
                "Gemini Vision reads it, extracts the itinerary and keeps only minimum "
                "evidence metadata. Review the draft, then tap Save trip."
            ),
            buttons=[TelegramButton(text="Enter it manually", callback_data="trip:manual:start")],
        )

    async def _start_draft(
        self, owner_user_id: str, telegram_user_id: str, telegram_chat_id: str, now: datetime
    ) -> TelegramView:
        current = await self._repository.get_trip_draft(telegram_user_id)
        if current is None:
            draft = TripDraft(
                draft_id=f"telegram-draft:{telegram_user_id}",
                owner_user_id=owner_user_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                created_at=now,
                updated_at=now,
            )
            saved = await self._repository.save_trip_draft(draft=draft, expected_version=None)
            if saved is None:
                raise TelegramTripError("could not start a private itinerary")
            current = saved
        if current.owner_user_id != owner_user_id or current.telegram_chat_id != telegram_chat_id:
            raise TelegramTripError("this itinerary belongs to another traveler")
        return self._draft_view(current)

    async def _save_draft(
        self,
        owner_user_id: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
    ) -> TelegramView:
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if (
            draft is None
            or draft.telegram_chat_id != telegram_chat_id
            or draft.owner_user_id != owner_user_id
        ):
            raise TelegramTripError("there is no private itinerary to save")
        try:
            result = await self._intake.import_trip(
                TripImportRequest(flights=draft.flights, hotel=draft.hotel),
                owner_user_id=owner_user_id,
            )
        except (ValidationError, TripImportConflict) as exc:
            raise TelegramTripError(
                "your itinerary cannot be saved; check the itinerary details"
            ) from exc
        trip = await self._repository.get_trip(result.trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise TelegramTripError("your itinerary was saved but monitoring could not be verified")
        # A planning confirmation is an estimate, not a second active trip. Once
        # the real booking arrives, close the private planned record so status
        # views and watchlists contain only the verified itinerary.
        if draft.planned_trip_id:
            planned = await self._repository.get_trip(draft.planned_trip_id)
            if planned is not None and planned.owner_user_id == owner_user_id:
                await self._repository.seed_trip(
                    planned.model_copy(
                        update={"status": TripStatus.CLOSED, "updated_at": now}
                    )
                )
        first_flight_id = next(
            (item.item_id for item in trip.items if item.type.value == "FLIGHT"), None
        )
        for source in draft.source_files:
            await self._repository.save_trip_document_once(
                TripDocument(
                    document_id=f"{trip.trip_id}:telegram:{source.source_id}",
                    trip_id=trip.trip_id,
                    owner_user_id=owner_user_id,
                    kind=source.kind,
                    linked_item_id=first_flight_id,
                    display_name=source.display_name,
                    source="TELEGRAM",
                    source_id=source.source_id,
                    mime_type=source.mime_type,
                    created_at=source.received_at,
                )
            )
        subscriptions = await self._monitoring.register_stored_schedule(trip, now=now)
        if self._amadeus_enabled:
            subscriptions = [
                await self._monitoring.activate_live_flight_status(
                    trip_id=subscription.trip_id,
                    item_id=subscription.item_id,
                    owner_user_id=owner_user_id,
                    now=now,
                )
                if subscription.item_id
                and any(
                    item.item_id == subscription.item_id and item.type.value == "FLIGHT"
                    for item in trip.items
                )
                else subscription
                for subscription in subscriptions
            ]
        watchpoints = self._watch_planner.build(
            trip, now=now, amadeus_enabled=self._amadeus_enabled
        )
        if not watchpoints:
            raise TelegramTripError(
                "your itinerary was saved but no monitoring checks could be initialized"
            )
        for watchpoint in watchpoints:
            await self._repository.put_watchpoint(watchpoint)
        # Clear the draft only after the durable trip and its first watchpoints
        # exist. If a worker dies earlier, retrying Save remains idempotent and
        # cannot leave a traveler with a saved trip that is not being watched.
        if not await self._repository.clear_trip_draft(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            expected_version=draft.version,
        ):
            raise TelegramTripError(
                "your itinerary changed in another message; review it before saving"
            )
        coverage = "\n".join(
            f"• {self._monitoring.coverage_label(subscription)}" for subscription in subscriptions
        )
        connection = await self._repository.get_ai_connection(telegram_user_id)
        connected = connection is not None and connection.status == AiConnectionStatus.CONNECTED
        shared_judge_watch = self._judge_mode
        watch_status = (
            "Shared judge Search Watch is enabled with a bounded project quota. I will begin "
            "with a focused public-source check and message you only when a decision is needed."
            if shared_judge_watch
            else "Personal Search Watch is enabled. I will begin with a focused public-source "
            "check now and message you only when a decision is needed."
            if connected
            else "The itinerary is ready for autonomous impact analysis. To begin personal "
            "Search Watch, connect your Gemini key; no booking or payment is performed "
            "without your policy and approval."
        )
        buttons = [TelegramButton(text="Add another trip", callback_data="trip:manual:start")]
        if not connected and not shared_judge_watch:
            buttons.insert(0, TelegramButton(text="Connect Gemini", callback_data="ai:menu"))
        return TelegramView(
            text=(
                f"Trip protected. I saved {result.item_count} itinerary item(s).\n\n"
                f"Monitoring coverage\n{coverage}\n\n"
                f"I created {len(watchpoints)} focused checks. {watch_status}"
            ),
            button_rows=[buttons],
        )

    async def _cancel_draft(self, telegram_user_id: str, telegram_chat_id: str) -> TelegramView:
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if draft is not None:
            await self._repository.clear_trip_draft(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                expected_version=draft.version,
            )
        return TelegramView(text="Private itinerary discarded. No trip was saved.")

    async def _add_pilot(self, owner_user_id: str, telegram_user_id: str) -> TelegramView:
        trip_id = f"pilot-trip:{telegram_user_id}"
        existing = await self._repository.get_trip(trip_id)
        if existing is not None and existing.owner_user_id != owner_user_id:
            raise TelegramTripError("trip belongs to another traveler")
        if existing is None:
            await self._repository.seed_trip(
                build_owned_demo_trip(owner_user_id=owner_user_id, trip_id=trip_id)
            )
        return TelegramView(
            text=(
                "Pilot trip added: Warsaw → Munich → Lisbon. "
                "It uses no real booking or payment data."
            ),
            buttons=[TelegramButton(text="Recovery settings", callback_data="onboard:restart")],
        )

    @staticmethod
    def _parse_flight(text: str) -> FlightImport:
        parts = [part.strip() for part in text.removeprefix("/flight ").split("|")]
        if len(parts) != 6:
            raise TelegramTripError("flight needs six fields")
        return FlightImport(
            flight_number=parts[0],
            provider=parts[1],
            origin=parts[2],
            destination=parts[3],
            departure_at=parts[4],
            arrival_at=parts[5],
        )

    @staticmethod
    def _parse_hotel(text: str) -> HotelImport:
        parts = [part.strip() for part in text.removeprefix("/hotel ").split("|")]
        if len(parts) != 4:
            raise TelegramTripError("hotel needs four fields")
        return HotelImport(
            provider=parts[0], name=parts[1], check_in_at=parts[2], check_out_at=parts[3]
        )

    @staticmethod
    def _flight_instruction() -> TelegramView:
        return TelegramView(
            text=(
                "Send one flight in this exact format:\n"
                "/flight LO351 | LOT | WAW | MUC | 2026-08-20T15:00+02:00 | "
                "2026-08-20T18:00+02:00"
            )
        )

    @staticmethod
    def _hotel_instruction() -> TelegramView:
        return TelegramView(
            text=(
                "Send an optional hotel in this exact format:\n"
                "/hotel Booking.com | Hotel Bayerischer Hof | 2026-08-20T22:00+02:00 | "
                "2026-08-23T10:00+02:00"
            )
        )

    def _draft_view(self, draft: TripDraft) -> TelegramView:
        lines = ["Your private itinerary draft"]
        if draft.planning_request is not None:
            selected = next(
                (
                    option
                    for option in draft.planning_options
                    if option.option_id == draft.selected_plan_id
                ),
                None,
            )
            plan_label = selected.title if selected is not None else "planning estimate"
            lines.append(f"• Planning target: {draft.planning_request.destination} · {plan_label}")
            if draft.planned_trip_id:
                lines.append(
                    "• Saved as a plan only — forward the real booking to start monitoring"
                )
        elif draft.planning_context is not None and draft.planning_context.destination:
            lines.append(f"• Planning brief: {draft.planning_context.destination}")
        lines.extend(
            f"• {flight.flight_number}: {flight.origin} → {flight.destination}"
            for flight in draft.flights
        )
        if draft.hotel is not None:
            lines.append(f"• Hotel: {draft.hotel.name}")
            if draft.hotel.contact_email:
                lines.append(
                    "• Hotel contact for a future Gmail draft: "
                    f"{draft.hotel.contact_email}"
                )
        if draft.source_files:
            lines.append(
                f"• Evidence: {len(draft.source_files)} forwarded file(s) kept as metadata"
            )
        if not draft.flights:
            lines.append("• No flights yet")
        buttons = [
            TelegramButton(text="Forward another booking", callback_data="trip:forward_help"),
            TelegramButton(text="Add flight", callback_data="trip:manual:add_flight"),
        ]
        if draft.flights or draft.hotel is not None:
            buttons.extend(
                [
                    *(
                        [TelegramButton(text="Add hotel", callback_data="trip:manual:add_hotel")]
                        if draft.hotel is None
                        else []
                    ),
                    TelegramButton(text="Save trip", callback_data="trip:manual:save"),
                ]
            )
        buttons.append(TelegramButton(text="Discard", callback_data="trip:manual:cancel"))
        return TelegramView(text="\n".join(lines), buttons=buttons)

    @staticmethod
    def _source_file(
        *,
        media_bytes: bytes,
        mime_type: str,
        caption: str,
        source_id: str | None,
        source_name: str | None,
        received_at: datetime,
    ) -> TripSourceFile:
        normalized_mime = (mime_type or "application/octet-stream").lower()
        stable_input = source_id or hashlib.sha256(media_bytes).hexdigest()
        stable_id = hashlib.sha256(f"{stable_input}:{normalized_mime}".encode()).hexdigest()[:32]
        lower = f"{source_name or ''} {caption}".lower()
        if "hotel" in lower or "booking.com" in lower or "airbnb" in lower:
            kind = "HOTEL_CONFIRMATION"
        elif "boarding" in lower or "pass" in lower or "pkpass" in lower:
            kind = "BOARDING_PASS"
        elif normalized_mime in {
            "application/pdf",
            "application/vnd.apple.pkpass",
            "application/zip",
        }:
            kind = "FLIGHT_TICKET"
        elif normalized_mime.startswith("image/"):
            kind = "BOARDING_PASS"
        else:
            kind = "OTHER"
        return TripSourceFile(
            source_id=stable_id,
            display_name=source_name or f"Telegram {kind.replace('_', ' ').lower()}",
            mime_type=normalized_mime,
            kind=kind,
            received_at=received_at,
        )
