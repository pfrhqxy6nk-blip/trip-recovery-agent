from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from html import escape
from typing import Protocol
from urllib.parse import quote_plus, urlparse

from pydantic import ValidationError

from app.models.domain import Dependency, TravelItem, Trip
from app.models.enums import DependencyType, ItemType, TripStatus
from app.models.planning import (
    FlexibleTravelPlanRequest,
    PlanningRequest,
    TravelPlanContext,
    TravelPlanOption,
    TravelPlanRequest,
    TravelPlanStay,
    TravelPlanTransport,
)
from app.models.telegram import TelegramButton, TelegramView, TravelerProfile
from app.models.trip_intake import TripDraft
from app.services.canonical_hash import canonical_hash
from app.services.judge_quota import claim_judge_vertex_slot
from app.services.ports import IncidentRepository

logger = logging.getLogger(__name__)


class TelegramPlanningError(ValueError):
    pass


class TripPlanner(Protocol):
    async def generate(
        self, *, request: PlanningRequest, now: datetime, telegram_user_id: str | None = None
    ) -> list[TravelPlanOption]: ...


class DeterministicTripPlanner:
    """Safe fallback used locally and when shared Vertex quota is unavailable.

    These are planning estimates, never bookings. Keeping the fallback deterministic makes
    the judge replay reliable while the live provider can add Search-grounded sources.
    """

    async def generate(
        self, *, request: PlanningRequest, now: datetime, telegram_user_id: str | None = None
    ) -> list[TravelPlanOption]:
        del telegram_user_id
        interests = ", ".join(request.interests[:3]) or "local highlights"
        if isinstance(request, FlexibleTravelPlanRequest):
            nights = request.nights
            travel_window = "flexible dates"
            date_query = f"{nights} nights"
            start_date = now.astimezone(UTC).date() + timedelta(days=21)
            end_date = start_date + timedelta(days=nights)
        else:
            nights = (request.end_date - request.start_date).days
            travel_window = f"{request.start_date} to {request.end_date}"
            date_query = travel_window
            start_date = request.start_date
            end_date = request.end_date
        origin = request.origin or "your departure city"
        flight_query = quote_plus(f"{origin} to {request.destination} {date_query}")
        destination_query = quote_plus(request.destination)
        flight_search = f"https://www.google.com/travel/flights?q={flight_query}"
        hotel_search = f"https://www.google.com/travel/hotels/{destination_query}"
        train_query = quote_plus(f"{origin} to {request.destination} train {date_query}")
        train_search = f"https://www.google.com/search?q={train_query}"
        weather_search = f"https://www.google.com/search?q={destination_query}+weather"

        def at(hour: int, minute: int = 0) -> datetime:
            return datetime(
                start_date.year,
                start_date.month,
                start_date.day,
                hour,
                minute,
                tzinfo=UTC,
            )

        def stay(
            name: str, provider: str, price: int, cancellation: str, url: str
        ) -> TravelPlanStay:
            return TravelPlanStay(
                provider=provider,
                name=name,
                check_in=start_date,
                check_out=end_date,
                nights=nights,
                price_eur=price,
                cancellation=cancellation,
                booking_url=url,
            )

        def transport(
            *,
            mode: str,
            provider: str,
            service: str,
            depart: datetime,
            arrive: datetime,
            price: int,
            url: str,
            conditions: str,
        ) -> TravelPlanTransport:
            return TravelPlanTransport(
                mode=mode,
                provider=provider,
                service=service,
                origin=origin,
                destination=request.destination,
                departure_at=depart,
                arrival_at=arrive,
                price_eur=price,
                booking_url=url,
                conditions=conditions,
            )

        destination_key = request.destination.strip().lower()
        stay_names = (
            ("ibis Paris République", "Hôtel des Arts Montmartre", "The People Paris Nation")
            if destination_key == "paris"
            else (
                f"ibis {request.destination}",
                f"{request.destination} Central Hotel",
                f"The People {request.destination}",
            )
        )

        # These examples deliberately look like real choices (carrier, service,
        # date, time and price) while every card remains visibly an estimate.
        # They make the offline path useful for a judge without fabricating a
        # confirmed booking or a live seat inventory.
        candidates = [
            (
                "balanced",
                "Balanced route",
                transport(
                    mode="FLIGHT",
                    provider="Google Flights",
                    service="Flight search",
                    depart=at(10, 20),
                    arrive=at(12, 35),
                    price=190,
                    url=flight_search,
                    conditions=(
                        "Indicative flight estimate; verify live fare, times and baggage rules."
                    ),
                ),
                stay(
                    stay_names[0],
                    "Booking.com",
                    300,
                    "Free cancellation shown in search; verify before payment.",
                    hotel_search,
                ),
                "A direct flight plus one central base keeps the plan easy to recover.",
                "One main base and a generous arrival buffer make disruption recovery easier.",
            ),
            (
                "flexible",
                "Flexible recovery-first",
                transport(
                    mode="TRAIN",
                    provider="Rail search",
                    service="Rail itinerary search",
                    depart=at(7, 10),
                    arrive=at(15, 40),
                    price=165,
                    url=train_search,
                    conditions=(
                        "Indicative rail estimate; verify availability and border requirements."
                    ),
                ),
                stay(
                    stay_names[1],
                    "Google Hotels",
                    360,
                    "Refundable rate requested; verify the cancellation deadline.",
                    hotel_search,
                ),
                "A rail-first option with a refundable stay leaves more recovery slack.",
                "Refundable choices and extra slack reduce the cost of a disruption.",
            ),
            (
                "value",
                "Value route",
                transport(
                    mode="BUS",
                    provider="Coach search",
                    service="Overnight coach search",
                    depart=at(19, 0),
                    arrive=at(8, 30) + timedelta(days=1),
                    price=85,
                    url=train_search,
                    conditions="Indicative coach estimate; verify departure time and change terms.",
                ),
                stay(
                    stay_names[2],
                    "Hostelworld",
                    240,
                    "Non-refundable estimate; verify room terms before booking.",
                    hotel_search,
                ),
                "A low-cost overnight connection trades time for the largest budget margin.",
                "Lowest estimate, but more travel time means less recovery slack.",
            ),
        ]
        return [
            TravelPlanOption(
                option_id=option_id,
                title=title,
                summary=f"{summary} Focus: {interests}.",
                route=f"{candidate.origin} → {candidate.destination}",
                estimated_total_eur=candidate.price_eur + accommodation.price_eur,
                travel_time_hours=round(
                    (candidate.arrival_at - candidate.departure_at).total_seconds() / 3600,
                    2,
                ),
                resilience_note=resilience,
                weather_note=(
                    f"Search window: {travel_window}. Re-check route-aware weather before booking."
                ),
                source_links=[candidate.booking_url, accommodation.booking_url, weather_search],
                generated_at=now,
                transport=candidate,
                stay=accommodation,
            )
            for option_id, title, candidate, accommodation, summary, resilience in candidates
        ]


class VertexTripPlanner:
    """Shared-credit, Search-grounded planner for the judge mode.

    It returns estimates only; the workflow never treats model output as a booking.
    """

    _SEARCH_MODEL = "gemini-3.5-flash-lite"
    _NORMALIZER_MODEL = "gemini-3.5-flash-lite"

    def __init__(
        self,
        repository: IncidentRepository,
        *,
        project: str,
        location: str,
        model: str,
        daily_limit: int,
        daily_user_limit: int | None = None,
        max_output_tokens: int,
    ) -> None:
        from google import genai

        self._repository = repository
        # Vertex AI Search grounding is exposed on the v1 API surface.  Pin the
        # API version explicitly so Cloud Run does not inherit an older preview
        # endpoint from the SDK environment.
        from google.genai import types

        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        self._model = model
        self._daily_limit = daily_limit
        self._daily_user_limit = daily_user_limit or daily_limit
        self._max_output_tokens = max_output_tokens
        self._fallback = DeterministicTripPlanner()

    async def generate(
        self, *, request: PlanningRequest, now: datetime, telegram_user_id: str | None = None
    ) -> list[TravelPlanOption]:
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if not await claim_judge_vertex_slot(
            self._repository,
            telegram_user_id=telegram_user_id or "anonymous",
            window_started_at=day,
            global_limit=self._daily_limit,
            per_user_limit=self._daily_user_limit,
            capability="planning",
        ):
            logger.warning(
                "vertex planning shared budget exhausted; returning deterministic estimate",
                extra={
                    "provider": "vertex-planner",
                    "error_code": "JUDGE_QUOTA_EXHAUSTED",
                    "result_class": "ESTIMATE",
                },
            )
            return await self._fallback.generate(request=request, now=now)
        failure_stage = "search"
        try:
            from google.genai import types

            if isinstance(request, FlexibleTravelPlanRequest):
                date_context = f"flexible dates; duration: {request.nights} nights"
                nights = request.nights
            else:
                date_context = (
                    f"{request.start_date} to {request.end_date}; "
                    f"duration: {(request.end_date - request.start_date).days} nights"
                )
                nights = (request.end_date - request.start_date).days
            prompt = (
                "Act only as the live research stage of a careful travel agent. MANDATORY: use "
                "Google Search before answering and cite the public pages used. Do not answer "
                "from memory and do not return JSON. Research exactly three practical trip "
                "bundles. For each bundle give a concrete travel date, transport provider and "
                "service, departure/arrival times, public price estimate and source; plus a real "
                "central hotel, stay dates, public total price, refund terms and source. Include "
                "one short weather or disruption note supported by a source. Rank the bundles "
                "for the traveler and keep the research notes concise. "
                "For flexible dates, choose real concrete travel dates between "
                f"{(now.astimezone(UTC).date() + timedelta(days=14)).isoformat()} and "
                f"{(now.astimezone(UTC).date() + timedelta(days=120)).isoformat()} that fit the "
                "brief, and state those dates in every bundle. Never claim a seat or room is "
                "reserved. Use direct provider or Google Travel pages, never invented domains. "
                f"Origin: {request.origin or 'not specified'}; "
                f"destination: {request.destination}; travel window: {date_context}; "
                f"nights: {nights}; "
                f"budget EUR: {request.budget_eur}; interests: "
                f"{', '.join(request.interests) or 'general sightseeing'}."
            )
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    # Flash-Lite has the same Search-grounding capability but
                    # materially higher shared throughput. The configured
                    # primary model remains available to the rest of the agent;
                    # this latency-sensitive shortlist uses the dedicated
                    # research model so a Telegram turn does not stall on shared
                    # Flash capacity.
                    model=self._SEARCH_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        # Grounded generation is more reliable at deterministic
                        # temperature; the planner is an extraction task, not a
                        # creative writing task.
                        temperature=0.0,
                        max_output_tokens=self._max_output_tokens,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                    ),
                ),
                # Search grounding regularly needs more than a plain Gemini
                # response because it has to resolve and cite live sources.
                # Eighteen seconds made healthy searches look like outages in
                # Telegram. Keep the request bounded, but give it enough time
                # to return all three transport-and-stay options.
                timeout=30,
            )
            failure_stage = "grounding"
            sources = self._grounding_sources(response)
            if not sources:
                raise ValueError("planner response had no grounded sources")
            grounded_text = response.text or ""
            failure_stage = "parsing"
            try:
                data = self._response_array(grounded_text)
            except (json.JSONDecodeError, ValueError):
                failure_stage = "normalization"
                # Search-grounded Gemini responses can contain excellent live
                # candidates and citations but still wrap, truncate or slightly
                # deform the requested JSON. Preserve the citations from that
                # first call, then use a short, non-grounded normalization call
                # whose only authority is formatting the already-found facts.
                repair_prompt = (
                    "Convert the travel-search result below into valid JSON only. Return exactly "
                    "one array with exactly three objects and preserve all facts, prices, dates, "
                    "providers and URLs from the input. Do not add or research anything. Each "
                    "object must have option_id, title, summary, route, estimated_total_eur, "
                    "travel_time_hours, resilience_note, weather_note, source_links, transport "
                    "and stay. transport needs mode, provider, service, origin, destination, "
                    "departure_at, arrival_at, price_eur, booking_url and conditions. stay needs "
                    "provider, name, check_in, check_out, nights, price_eur, cancellation and "
                    "booking_url. Use ISO-8601 timestamps with timezones and ISO dates. Every "
                    "booking_url and source_links entry must be copied from VERIFIED SOURCE URLS; "
                    "never create a URL.\n\n"
                    f"VERIFIED SOURCE URLS:\n{chr(10).join(sources)}\n\n"
                    f"GROUNDED RESULT:\n{grounded_text[:24000]}"
                )
                repaired = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        # Normalization is a low-latency extraction task. Keep
                        # the primary Flash model for Search/reasoning and use
                        # Flash-Lite only to make its already-grounded facts
                        # conform to the strict application schema.
                        model=self._NORMALIZER_MODEL,
                        contents=repair_prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.0,
                            max_output_tokens=self._max_output_tokens,
                            response_mime_type="application/json",
                        ),
                    ),
                    timeout=15,
                )
                data = self._response_array(repaired.text or "")
            failure_stage = "validation"
            options: list[TravelPlanOption] = []
            for index, item in enumerate(data[:3], start=1):
                if not isinstance(item, dict):
                    continue
                item["option_id"] = str(item.get("option_id") or f"live-{index}")[:40]
                item["generated_at"] = now
                item["availability"] = "LIVE"
                self._normalize_live_item(item, sources=sources)
                option = TravelPlanOption.model_validate(item)
                # A grounded paragraph without item-level candidates is not a
                # usable search result. Fail closed to the explicit estimate
                # path instead of presenting invented inventory as live.
                if option.transport is None or option.stay is None:
                    raise ValueError("planner response omitted concrete transport or stay")
                options.append(option)
            if len(options) == 3:
                return options
        except Exception as exc:
            # Do not expose SDK text (it can contain request URLs or credentials),
            # but leave an auditable bounded marker for Cloud Logging. The Telegram
            # view labels these options as ESTIMATE, so a provider outage can never
            # be mistaken for live availability.
            failure_detail = None
            if isinstance(exc, ValidationError):
                failure_detail = ";".join(
                    f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
                    for error in exc.errors(include_input=False, include_url=False)[:8]
                )[:400]
            logger.warning(
                "vertex planning failed; returning deterministic estimate",
                extra={
                    "provider": "vertex-planner",
                    "error_code": "VERTEX_PLANNER_FALLBACK",
                    "result_class": "ESTIMATE",
                    "error_class": type(exc).__name__,
                    "failure_stage": failure_stage,
                    "failure_detail": failure_detail,
                },
            )
        return await self._fallback.generate(
            request=request, now=now, telegram_user_id=telegram_user_id
        )

    @staticmethod
    def _response_array(raw_response: str) -> list[object]:
        """Extract the first JSON array without accepting surrounding prose.

        Gemini can wrap otherwise valid JSON in a Markdown fence or a one-line
        lead-in even when explicitly told not to.  A bounded raw decode keeps
        that presentation quirk from throwing away grounded results, while an
        invalid or truncated response still fails closed to estimates.
        """
        raw = raw_response.strip()
        start = raw.find("[")
        if start < 0:
            raise ValueError("planner response contained no JSON array")
        decoded, _ = json.JSONDecoder().raw_decode(raw[start:])
        if not isinstance(decoded, list):
            raise ValueError("planner response was not a list")
        return decoded

    @staticmethod
    def _grounding_sources(response: object) -> list[str]:
        links: list[str] = []
        for candidate in getattr(response, "candidates", []) or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            for chunk in getattr(metadata, "grounding_chunks", []) or []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if isinstance(uri, str) and uri.startswith("https://") and uri not in links:
                    links.append(uri)
        return links[:6]

    @staticmethod
    def _normalize_live_item(item: dict[str, object], *, sources: list[str]) -> None:
        """Canonicalize bounded model variations without inventing travel facts."""

        for field, limit in (
            ("title", 120),
            ("summary", 600),
            ("route", 200),
            ("resilience_note", 240),
            ("weather_note", 240),
        ):
            value = item.get(field)
            if isinstance(value, str):
                item[field] = value.strip()[:limit]

        verified_sources = [
            source for source in sources if isinstance(source, str) and source.startswith("https://")
        ][:6]
        item["source_links"] = verified_sources

        transport = item.get("transport")
        if isinstance(transport, dict):
            raw_mode = str(transport.get("mode", "")).strip().upper()
            if raw_mode in {"AIR", "AIRPLANE", "PLANE", "FLY"} or "FLIGHT" in raw_mode:
                transport["mode"] = "FLIGHT"
            elif raw_mode in {"RAIL", "RAILWAY"} or "TRAIN" in raw_mode:
                transport["mode"] = "TRAIN"
            elif raw_mode in {"COACH", "AUTOBUS"} or "BUS" in raw_mode:
                transport["mode"] = "BUS"
            for timestamp_field in ("departure_at", "arrival_at"):
                timestamp = transport.get(timestamp_field)
                if not isinstance(timestamp, str):
                    continue
                normalized = timestamp.strip()
                try:
                    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.tzinfo is None:
                    transport[timestamp_field] = f"{normalized}Z"
            if verified_sources:
                transport["booking_url"] = verified_sources[0]

        stay = item.get("stay")
        if isinstance(stay, dict) and verified_sources:
            stay["booking_url"] = verified_sources[min(1, len(verified_sources) - 1)]


class TelegramPlanningService:
    """Telegram control-plane for planning before a real booking is monitored."""

    def __init__(
        self, repository: IncidentRepository, *, planner: TripPlanner | None = None
    ) -> None:
        self._repository = repository
        self._planner = planner or DeterministicTripPlanner()

    async def handle(
        self, *, telegram_user_id: str, telegram_chat_id: str, callback_data: str, now: datetime
    ) -> TelegramView:
        traveler = await self._active_traveler(telegram_user_id, telegram_chat_id)
        if callback_data in {"plan:start", "plan:menu"}:
            return self._instruction()
        if callback_data == "plan:preferences":
            return self._preferences_view(traveler)
        if callback_data == "plan:recommendations_on":
            updated = traveler.model_copy(
                update={"recommendations_enabled": True, "updated_at": now}
            )
            await self._repository.save_traveler(updated)
            return self._preferences_view(updated)
        if callback_data == "plan:recommendations_off":
            updated = traveler.model_copy(
                update={"recommendations_enabled": False, "updated_at": now}
            )
            await self._repository.save_traveler(updated)
            return self._preferences_view(updated)
        if callback_data.startswith("plan:select:"):
            return await self._select(
                telegram_user_id,
                telegram_chat_id,
                callback_data.removeprefix("plan:select:"),
                now=now,
            )
        if callback_data == "plan:save":
            return await self._save_plan(telegram_user_id, telegram_chat_id, now=now)
        raise TelegramPlanningError("this planning action is unavailable")

    async def handle_message(
        self, *, telegram_user_id: str, telegram_chat_id: str, text: str, now: datetime
    ) -> TelegramView:
        traveler = await self._active_traveler(telegram_user_id, telegram_chat_id)
        if self._looks_like_recommendation(text):
            return self._preferences_view(traveler)
        request: PlanningRequest | None = self._parse_request(text)
        draft = await self._get_or_create_draft(traveler, now)
        # A transport search without the departure city is not a useful offer.
        # Ask the single missing question instead of inventing a starting point.
        if request is not None and request.origin is None:
            context = self._context_from_request(request)
            assert context is not None
            saved_context = await self._repository.save_trip_draft(
                draft=draft.model_copy(
                    update={
                        "planning_context": context,
                        "planning_request": None,
                        "planning_options": [],
                        "selected_plan_id": None,
                        "planning_saved_at": None,
                        "updated_at": now,
                    }
                ),
                expected_version=draft.version,
            )
            if saved_context is None:
                raise TelegramPlanningError("your planning draft changed; please try again")
            return self._clarification_view(context)
        if request is None:
            previous_context = draft.planning_context or self._context_from_request(
                draft.planning_request
            )
            # When the bot asks only for a departure city, users naturally
            # answer with a bare value ("Kyiv", "Berlin") rather than "from
            # Kyiv". Treat a short plain-text reply as that missing field while
            # preserving the rest of the saved brief.
            if (
                previous_context is not None
                and previous_context.origin is None
                and re.fullmatch(r"[A-Za-zÀ-ÿА-яЁё' -]{2,80}", text.strip())
            ):
                context = previous_context.model_copy(update={"origin": text.strip()})
            else:
                context = self._natural_context(text, previous_context)
            request = self._request_from_context(context)
            if request is None:
                saved_context = await self._repository.save_trip_draft(
                    draft=draft.model_copy(update={"planning_context": context, "updated_at": now}),
                    expected_version=draft.version,
                )
                if saved_context is None:
                    raise TelegramPlanningError("your planning draft changed; please try again")
                return self._clarification_view(context)
        options = await self._planner.generate(
            request=request, now=now, telegram_user_id=telegram_user_id
        )
        saved = await self._repository.save_trip_draft(
            draft=draft.model_copy(
                update={
                    "planning_context": None,
                    "planning_request": request,
                    "planning_options": options,
                    "selected_plan_id": None,
                    "planning_saved_at": None,
                    "updated_at": now,
                }
            ),
            expected_version=draft.version,
        )
        if saved is None:
            raise TelegramPlanningError("your planning draft changed; please try again")
        return self._options_view(saved)

    async def _select(
        self,
        telegram_user_id: str,
        telegram_chat_id: str,
        option_id: str,
        *,
        now: datetime,
    ) -> TelegramView:
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if draft is None or draft.telegram_chat_id != telegram_chat_id:
            raise TelegramPlanningError("start planning first")
        if not any(option.option_id == option_id for option in draft.planning_options):
            raise TelegramPlanningError("that plan option is no longer available")
        saved = await self._repository.save_trip_draft(
            draft=draft.model_copy(update={"selected_plan_id": option_id}),
            expected_version=draft.version,
        )
        if saved is None:
            raise TelegramPlanningError("your planning draft changed; please try again")
        # Choosing a candidate is the traveller's planning confirmation. Save
        # it directly instead of inserting two more button screens.
        return await self._save_plan(telegram_user_id, telegram_chat_id, now=now)

    async def _save_plan(
        self, telegram_user_id: str, telegram_chat_id: str, *, now: datetime
    ) -> TelegramView:
        traveler = await self._active_traveler(telegram_user_id, telegram_chat_id)
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if (
            draft is None
            or draft.telegram_chat_id != telegram_chat_id
            or not draft.selected_plan_id
        ):
            raise TelegramPlanningError("choose a plan option first")
        # Telegram may deliver the same callback more than once (double tap,
        # retry, or a delayed update). Once a draft has been saved, return the
        # existing planned trip instead of creating another record.
        if draft.planning_saved_at is not None and draft.planned_trip_id:
            return TelegramView(
                text=(
                    "This plan is already saved.\n\n"
                    f"Your planned trip ({draft.planned_trip_id}) is ready. "
                    "Forward the actual booking when you have it; then I will "
                    "replace the estimate with verified itinerary data and activate watchpoints."
                ),
                parse_mode="HTML",
            )
        option = next(
            item for item in draft.planning_options if item.option_id == draft.selected_plan_id
        )
        planned_trip_id = await self._persist_planned_trip(
            draft=draft,
            option=option,
            owner_user_id=traveler.user_id,
        )
        saved = await self._repository.save_trip_draft(
            draft=draft.model_copy(
                update={
                    "planning_saved_at": now,
                    "planned_trip_id": planned_trip_id,
                    "updated_at": now,
                }
            ),
            expected_version=draft.version,
        )
        if saved is None:
            raise TelegramPlanningError("your plan changed; please choose it again")
        return TelegramView(
            text=(
                f"<b>Plan saved · {escape(option.title)}</b>\n"
                f"€{option.estimated_total_eur} estimate · your personal shortlist\n\n"
                f"{self._option_details(option)}\n\n"
                "When you have the actual booking, forward the confirmation here. I’ll verify the "
                "itinerary, "
                "replace this estimate with real details and start Trip Watch automatically."
            ),
            parse_mode="HTML",
        )

    async def _persist_planned_trip(
        self,
        *,
        draft: TripDraft,
        option: TravelPlanOption,
        owner_user_id: str,
    ) -> str:
        """Persist a plan as PLANNED, never as a confirmed reservation.

        Planning candidates have no PNR and no guaranteed inventory, so this
        record is intentionally excluded from autonomous disruption actions.
        A later booking intake creates the confirmed, watched trip.
        """
        if option.transport is None or option.stay is None:
            raise TelegramPlanningError("this plan has no concrete transport and stay details")
        identity = {
            "owner": owner_user_id,
            "request": draft.planning_request.model_dump(mode="json")
            if draft.planning_request is not None
            else None,
            "option": option.model_dump(mode="json"),
        }
        trip_id = f"planned-trip-{canonical_hash(identity)[:24]}"
        transport = option.transport
        stay = option.stay
        flight_id = f"planned-transport-{option.option_id}"
        hotel_id = f"planned-stay-{option.option_id}"
        hotel_start = datetime(
            stay.check_in.year, stay.check_in.month, stay.check_in.day, 12, tzinfo=UTC
        )
        hotel_end = datetime(
            stay.check_out.year, stay.check_out.month, stay.check_out.day, 12, tzinfo=UTC
        )
        planned = Trip(
            trip_id=trip_id,
            owner_user_id=owner_user_id,
            intake_hash=canonical_hash(identity),
            status=TripStatus.PLANNED,
            origin=transport.origin,
            destination=transport.destination,
            starts_at=transport.departure_at,
            ends_at=hotel_end,
            items=[
                TravelItem(
                    item_id=flight_id,
                    trip_id=trip_id,
                    type=ItemType.FLIGHT,
                    provider=transport.provider,
                    start_at=transport.departure_at,
                    end_at=transport.arrival_at,
                    origin=transport.origin,
                    destination=transport.destination,
                    external_id=transport.service,
                    status="PLANNED",
                ),
                TravelItem(
                    item_id=hotel_id,
                    trip_id=trip_id,
                    type=ItemType.HOTEL_ARRIVAL,
                    provider=stay.provider,
                    start_at=hotel_start,
                    end_at=hotel_end,
                    location=stay.name,
                    status="PLANNED",
                ),
            ],
            dependencies=[
                Dependency(
                    dependency_id="planned-transport-to-stay",
                    trip_id=trip_id,
                    from_item_id=flight_id,
                    to_item_id=hotel_id,
                    type=DependencyType.FOLLOW_ON,
                )
            ],
            created_at=draft.updated_at,
            updated_at=draft.updated_at,
        )
        await self._repository.create_trip_once(planned)
        stored = await self._repository.get_trip(trip_id)
        if stored is None or stored.owner_user_id != owner_user_id:
            raise TelegramPlanningError("the plan could not be saved privately")
        return trip_id

    async def _get_or_create_draft(self, traveler: TravelerProfile, now: datetime) -> TripDraft:
        current = await self._repository.get_trip_draft(traveler.telegram_user_id)
        if current is not None and current.telegram_chat_id == traveler.telegram_chat_id:
            return current
        draft = TripDraft(
            draft_id=f"telegram-draft:{traveler.telegram_user_id}",
            owner_user_id=traveler.user_id,
            telegram_user_id=traveler.telegram_user_id,
            telegram_chat_id=traveler.telegram_chat_id,
            created_at=now,
            updated_at=now,
        )
        saved = await self._repository.save_trip_draft(draft=draft, expected_version=None)
        if saved is None:
            raise TelegramPlanningError("could not start a private planning draft")
        return saved

    async def _active_traveler(
        self, telegram_user_id: str, telegram_chat_id: str
    ) -> TravelerProfile:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
            or traveler.onboarding_step.value != "COMPLETE"
        ):
            raise TelegramPlanningError("finish onboarding before planning a trip")
        return traveler

    @staticmethod
    def _instruction() -> TelegramView:
        return TelegramView(
            text=(
                "Tell me the trip you want to plan in one line — for example:\n\n"
                "I want to go to Paris for 6 nights, budget €600. Add a departure city or "
                "travel dates if you have them.\n\n"
                "When live Google Search is available, I will return three sourced options. "
                "Otherwise I show clearly labelled estimates with search links. They are not "
                "bookings; only a real forwarded reservation activates monitoring."
            )
        )

    @staticmethod
    def _options_view(draft: TripDraft) -> TelegramView:
        request = draft.planning_request
        if request is None:
            raise TelegramPlanningError("planning request is missing")
        trip_meta = (
            f"flexible dates · {request.nights} nights"
            if isinstance(request, FlexibleTravelPlanRequest)
            else f"{request.start_date} → {request.end_date}"
        )
        text = [
            f"<b>Planning {escape(request.destination)}</b> · {trip_meta} · "
            f"€{request.budget_eur}",
            "Three considered routes — transport, stay and recovery slack.",
            "Prices are estimates, not bookings. Choose one to save your shortlist.",
        ]
        if not any(option.availability == "LIVE" for option in draft.planning_options):
            text.insert(
                3,
                "Live Google Search is temporarily unavailable right now. These are clearly "
                "marked estimates; open the linked sources to verify today’s price.",
            )
        rows: list[list[TelegramButton]] = []
        for index, option in enumerate(draft.planning_options, start=1):
            source_label = "live" if option.availability == "LIVE" else "estimate"
            text.append(
                f"\n<b>{index} · {escape(option.title)}</b> · "
                f"€{option.estimated_total_eur} · {source_label}"
            )
            text.append(TelegramPlanningService._option_details(option))
            remaining = request.budget_eur - option.estimated_total_eur
            fit = "within budget" if remaining >= 0 else f"€{abs(remaining)} over budget"
            text.append(f"<i>{fit} · {escape(option.resilience_note)}</i>")
            if option.source_links:
                text.append(TelegramPlanningService._source_links_view(option))
            rows.append(
                [
                    TelegramButton(
                        text=f"Choose {option.title}",
                        callback_data=f"plan:select:{option.option_id}",
                    )
                ]
            )
        return TelegramView(text="\n".join(text), parse_mode="HTML", button_rows=rows)

    @staticmethod
    def _option_details(option: TravelPlanOption) -> str:
        """Render the concrete candidate compactly for a Telegram card."""
        transport = option.transport
        stay = option.stay
        if transport is None or stay is None:
            return (
                f"{escape(option.route)} · {option.travel_time_hours:g}h transport · "
                f"€{option.estimated_total_eur} estimate"
            )
        icon = {"FLIGHT": "✈️", "TRAIN": "🚆", "BUS": "🚌"}[transport.mode]
        depart = transport.departure_at.strftime("%d %b %H:%M")
        arrive = transport.arrival_at.strftime("%H:%M")
        transport_line = (
            f"{icon} <b>{escape(transport.provider)}</b> {escape(transport.service)} · "
            f"{escape(transport.origin)} → {escape(transport.destination)} · "
            f"{depart}–{arrive} · €{transport.price_eur}"
        )
        stay_line = (
            f"🏨 <b>{escape(stay.name)}</b> · {stay.nights} nights · €{stay.price_eur} · "
            f"{escape(stay.cancellation)}"
        )
        return f"{transport_line}\n{stay_line}"

    @staticmethod
    def _source_links_view(option: TravelPlanOption) -> str:
        """Keep evidence tappable without dumping long raw URLs into chat."""
        links: list[tuple[str, str]] = []
        if option.transport is not None:
            links.append(
                (
                    TelegramPlanningService._source_label(option.transport.booking_url),
                    option.transport.booking_url,
                )
            )
        if option.stay is not None:
            links.append(
                (
                    TelegramPlanningService._source_label(option.stay.booking_url),
                    option.stay.booking_url,
                )
            )
        known_urls = {url for _, url in links}
        for source in option.source_links:
            if source not in known_urls:
                links.append((TelegramPlanningService._source_label(source), source))
                known_urls.add(source)
        return "🔗 Sources: " + " · ".join(
            f'<a href="{escape(url, quote=True)}">{label}</a>' for label, url in links[:3]
        )

    @staticmethod
    def _source_label(url: str) -> str:
        host = urlparse(url).netloc.casefold().removeprefix("www.")
        labels = {
            "google.com": "Google Travel",
            "booking.com": "Booking.com",
            "airbnb.com": "Airbnb",
            "hostelworld.com": "Hostelworld",
        }
        return labels.get(host, host.split(".")[0].title() or "Source")

    @staticmethod
    def _preferences_view(traveler: TravelerProfile) -> TelegramView:
        status = "on" if traveler.recommendations_enabled else "off"
        return TelegramView(
            text=(
                f"Personal travel ideas are <b>{status}</b>.\n\n"
                "When enabled, I may use your saved destinations and interests for optional "
                "recommendations. I never book or spend money from a recommendation."
            ),
            parse_mode="HTML",
            buttons=[
                TelegramButton(
                    text="Turn off" if traveler.recommendations_enabled else "Turn on",
                    callback_data="plan:recommendations_off"
                    if traveler.recommendations_enabled
                    else "plan:recommendations_on",
                )
            ],
        )

    @staticmethod
    def _parse_request(text: str) -> TravelPlanRequest | None:
        raw = text.strip()
        if raw.startswith("/plan"):
            parts = [part.strip() for part in raw.removeprefix("/plan").strip().split("|")]
            if len(parts) < 4:
                return None
            interests = [item.strip() for item in parts[4].split(",")] if len(parts) > 4 else []
            try:
                return TravelPlanRequest(
                    destination=parts[0],
                    start_date=date.fromisoformat(parts[1]),
                    end_date=date.fromisoformat(parts[2]),
                    budget_eur=int(parts[3]),
                    interests=interests,
                )
            except (ValueError, ValidationError):
                raise TelegramPlanningError(
                    "use /plan Destination | YYYY-MM-DD | YYYY-MM-DD | budget | interests"
                ) from None
        match = re.search(
            r"(?:plan|спланируй)\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(?:to|до|-)\s+(\d{4}-\d{2}-\d{2}).*?(?:budget|бюджет)\s*(?:€|eur)?\s*(\d+)",
            raw,
            re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return TravelPlanRequest(
                destination=match.group(1).strip(),
                start_date=date.fromisoformat(match.group(2)),
                end_date=date.fromisoformat(match.group(3)),
                budget_eur=int(match.group(4)),
            )
        except (ValueError, ValidationError):
            raise TelegramPlanningError("I could not validate those dates or budget") from None

    @staticmethod
    def looks_like_planning(text: str) -> bool:
        """Route planning language without hijacking ordinary Telegram chat."""

        raw = text.strip()
        normalized = raw.casefold()
        if not normalized:
            return False
        if raw.startswith("/plan") or normalized.startswith(("plan ", "спланируй ")):
            return True
        if "plan trip" in normalized or "план поезд" in normalized:
            return True
        has_trip_shape = bool(
            re.search(r"\b\d+\s*(?:ноч(?:ей|и|ь)?|nights?|days?|дн(?:я|ей)?)\b", normalized)
            and re.search(r"(?:€|eur|евро|budget|бюджет|за\s+\d+)", normalized)
        )
        has_trip_verb = any(
            phrase in normalized
            for phrase in (
                "хочу в ",
                "хочу поехать",
                "поездку в ",
                "want to ",
                "i want ",
                "trip to ",
                "travel to ",
            )
        )
        # A follow-up such as "из Киева, 2026-10-10" must reach the persisted draft.
        is_follow_up = bool(re.search(r"^(?:из|from)\b", normalized))
        return (has_trip_verb and has_trip_shape) or is_follow_up

    @classmethod
    def _natural_context(cls, text: str, previous: TravelPlanContext | None) -> TravelPlanContext:
        """Extract only explicit facts; never invent a date, origin or budget."""

        normalized = " ".join(text.strip().split())
        destination = cls._extract_destination(normalized) or (
            previous.destination if previous else None
        )
        origin = cls._extract_origin(normalized) or (previous.origin if previous else None)
        nights = cls._extract_nights(normalized) or (previous.nights if previous else None)
        budget = cls._extract_budget(normalized) or (previous.budget_eur if previous else None)
        dates = [date.fromisoformat(item) for item in re.findall(r"\d{4}-\d{2}-\d{2}", normalized)]
        start_date = dates[0] if dates else (previous.start_date if previous else None)
        end_date = dates[1] if len(dates) > 1 else (previous.end_date if previous else None)
        if start_date is not None and end_date is None and nights is not None:
            end_date = start_date + timedelta(days=nights)
        if start_date is not None and end_date is not None and end_date > start_date:
            nights = (end_date - start_date).days
        try:
            return TravelPlanContext(
                destination=destination,
                origin=origin,
                start_date=start_date,
                end_date=end_date,
                nights=nights,
                budget_eur=budget,
                interests=previous.interests if previous else [],
            )
        except ValidationError as exc:
            # Keep the conversational flow recoverable when the user typed an invalid value.
            logger.info(
                "natural planning input needs clarification",
                extra={"error_code": "PLAN_INPUT_INVALID"},
            )
            fields = {
                key: value for key, value in (previous.model_dump() if previous else {}).items()
            }
            fields.update(
                {
                    "destination": destination,
                    "origin": origin,
                    "start_date": start_date,
                    "end_date": end_date,
                    "nights": None,
                    "budget_eur": budget,
                }
            )
            try:
                return TravelPlanContext.model_validate(fields)
            except ValidationError:
                del exc
                return TravelPlanContext(destination=destination, origin=origin, budget_eur=budget)

    @staticmethod
    def _request_from_context(context: TravelPlanContext) -> PlanningRequest | None:
        if context.destination is None or context.origin is None or context.budget_eur is None:
            return None
        if context.start_date is None and context.end_date is None and context.nights is not None:
            return FlexibleTravelPlanRequest(
                destination=context.destination,
                origin=context.origin,
                nights=context.nights,
                budget_eur=context.budget_eur,
                interests=context.interests,
            )
        if context.start_date is None or context.end_date is None:
            return None
        try:
            return TravelPlanRequest(
                destination=context.destination,
                origin=context.origin,
                start_date=context.start_date,
                end_date=context.end_date,
                budget_eur=context.budget_eur,
                interests=context.interests,
            )
        except ValidationError:
            return None

    @staticmethod
    def _context_from_request(request: PlanningRequest | None) -> TravelPlanContext | None:
        if request is None:
            return None
        if isinstance(request, FlexibleTravelPlanRequest):
            return TravelPlanContext(
                destination=request.destination,
                origin=request.origin,
                nights=request.nights,
                budget_eur=request.budget_eur,
                interests=request.interests,
            )
        return TravelPlanContext(
            destination=request.destination,
            origin=request.origin,
            start_date=request.start_date,
            end_date=request.end_date,
            nights=(request.end_date - request.start_date).days,
            budget_eur=request.budget_eur,
            interests=request.interests,
        )

    @staticmethod
    def _clarification_view(context: TravelPlanContext) -> TelegramView:
        missing: list[str] = []
        if not context.destination:
            missing.append("your destination")
        if not context.origin:
            missing.append("the city you will depart from")
        if not context.start_date or not context.end_date:
            if context.nights:
                # A duration is sufficient for a flexible shortlist; exact dates are optional.
                pass
            else:
                missing.append("your dates or number of nights")
        if context.budget_eur is None:
            missing.append("your budget in EUR")
        if len(missing) == 1:
            question = f"What is {missing[0]}?"
        else:
            question = "Please tell me " + ", ".join(missing) + "."
        return TelegramView(text=f"I have the start of your trip brief.\n\n{question}")

    @staticmethod
    def _extract_destination(text: str) -> str | None:
        # Handle the most natural English formulation before the general
        # "to Paris" matcher below. Without this branch, "go from Kyiv to
        # Paris" was captured as the destination "go".
        direct_route = re.search(
            r"\b(?:go|travel|fly|visit)\s+from\s+.+?\s+(?:to|in)\s+(.+?)"
            r"(?=\s+(?:for|на)\s+\d+\s*(?:nights?|ноч(?:ей|и|ь)?)|"
            r"\s+(?:under|budget|за|до|бюджет)\b|,|$)",
            text,
            re.IGNORECASE,
        )
        if direct_route:
            return " ".join(direct_route.group(1).split()).strip(" ,") or None
        match = re.search(
            r"(?:хочу(?:\s+поехать)?|поездк\w*|trip|travel)?\s*(?:в|in|to)\s+(.+?)"
            r"(?=\s+(?:на|for)\s+\d+\s*(?:ноч|night|дн|day)|\s+(?:за|under|до|budget|бюджет)\b|"
            r"\s+(?:из|from)\b|,|$)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        destination = " ".join(match.group(1).split()).strip(" ,")
        destination = re.sub(
            r"^(?:go\s+to|travel\s+to|want\s+to)\s+",
            "",
            destination,
            flags=re.IGNORECASE,
        )
        return destination or None

    @staticmethod
    def _extract_origin(text: str) -> str | None:
        match = re.search(
            # Stop at a real trip boundary.  A traveller naturally writes
            # "from Warsaw in October for €600"; treating the whole tail as
            # the city makes every downstream transport option nonsensical.
            r"(?:из|from)\s+(.+?)(?=\s+(?:to|в)\s+|\s+(?:на|for)\s+\d|"
            r"\s+(?:in|during|around)\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
            r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
            r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|"
            r"\s+for\s+(?:(?:€|eur)\s*)?\d|"
            r"\s+(?:budget|under|за|до|бюджет)\b|\s+\d{4}-\d{2}-\d{2}|,|[.!?]|$)",
            text,
            re.IGNORECASE,
        )
        return " ".join(match.group(1).split()).strip(" ,") if match else None

    @staticmethod
    def _extract_nights(text: str) -> int | None:
        match = re.search(r"\b(\d+)\s*-?\s*(ноч(?:ей|и|ь)?|nights?)\b", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        days = re.search(r"\b(\d+)\s*(дн(?:я|ей)?|days?)\b", text, re.IGNORECASE)
        return max(1, int(days.group(1)) - 1) if days else None

    @staticmethod
    def _extract_budget(text: str) -> int | None:
        patterns = (
            r"(?:€|eur|евро)\s*(\d[\d\s]*)",
            r"(\d[\d\s]*)\s*(?:€|eur|евро)",
            r"(?:за|under|до|budget|бюджет)\s*[:=]?\s*(?:€|eur|евро)?\s*(\d[\d\s]*)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1).replace(" ", ""))
        return None

    @staticmethod
    def _looks_like_recommendation(text: str) -> bool:
        normalized = text.casefold()
        return any(word in normalized for word in ("recommend", "ideas", "рекомендац", "идеи"))
