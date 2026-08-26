from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from urllib.parse import quote_plus

from pydantic import ValidationError

from app.models.planning import (
    FlexibleTravelPlanRequest,
    PlanningRequest,
    TravelPlanContext,
    TravelPlanOption,
    TravelPlanRequest,
)
from app.models.telegram import TelegramButton, TelegramView, TravelerProfile
from app.models.trip_intake import TripDraft
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
        else:
            nights = (request.end_date - request.start_date).days
            travel_window = f"{request.start_date} to {request.end_date}"
            date_query = travel_window
        budget = request.budget_eur
        share = max(1, budget // 3)
        origin = request.origin or "your departure city"
        flight_query = quote_plus(f"{origin} to {request.destination} {date_query}")
        destination_query = quote_plus(request.destination)
        links = [
            f"https://www.google.com/travel/flights?q={flight_query}",
            f"https://www.google.com/travel/hotels/{destination_query}",
            f"https://www.google.com/search?q={destination_query}+weather",
        ]
        return [
            TravelPlanOption(
                option_id="balanced",
                title="Balanced route",
                summary=f"A comfortable {nights}-night route focused on {interests}.",
                route=f"Arrival hub → {request.destination} → departure hub",
                estimated_total_eur=min(budget, max(share * 3, 220)),
                travel_time_hours=4.5,
                resilience_note=(
                    "One main base and a generous arrival buffer make disruption recovery easier."
                ),
                weather_note=(
                    f"Search window: {travel_window}. Check route-aware weather again "
                    "before booking."
                ),
                source_links=links,
                generated_at=now,
            ),
            TravelPlanOption(
                option_id="flexible",
                title="Flexible recovery-first",
                summary=f"Fewer connections, refundable stays and extra slack for {interests}.",
                route=f"Direct/one-stop arrival → {request.destination} → flexible return",
                estimated_total_eur=min(budget, max(share * 3 + 140, 320)),
                travel_time_hours=5.5,
                resilience_note=(
                    "Refundable choices and a 3-hour buffer reduce the cost of a disruption."
                ),
                weather_note=(
                    "The agent will watch severe weather affecting the route after a booking "
                    "is forwarded."
                ),
                source_links=links,
                generated_at=now,
            ),
            TravelPlanOption(
                option_id="value",
                title="Value route",
                summary=(
                    f"A lower-cost base with public transport and a compact {nights}-night plan."
                ),
                route=f"Value arrival hub → {request.destination} → value departure hub",
                estimated_total_eur=min(budget, max(share * 2, 180)),
                travel_time_hours=7.0,
                resilience_note="Lowest estimate, but more connections mean less recovery slack.",
                weather_note=(
                    "Weather and service alerts are checked once the itinerary is confirmed."
                ),
                source_links=links,
                generated_at=now,
            ),
        ]


class VertexTripPlanner:
    """Shared-credit, Search-grounded planner for the judge mode.

    It returns estimates only; the workflow never treats model output as a booking.
    """

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
        self._client = genai.Client(vertexai=True, project=project, location=location)
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
                "Create exactly three date-aware travel planning estimates, not bookings. Search "
                "the public web for current flight and hotel options, but never claim a seat or "
                "room is reserved. Return JSON only as "
                "an array with option_id, title, summary, route, estimated_total_eur, "
                "travel_time_hours, resilience_note, weather_note, source_links. Use HTTPS links "
                f"and keep each string short. Origin: {request.origin or 'not specified'}; "
                f"destination: {request.destination}; travel window: {date_context}; "
                f"nights: {nights}; "
                f"budget EUR: {request.budget_eur}; interests: "
                f"{', '.join(request.interests) or 'general sightseeing'}."
            )
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=self._max_output_tokens,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            raw = (response.text or "").strip().removeprefix("```json").removesuffix("```").strip()
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("planner response was not a list")
            sources = self._grounding_sources(response)
            if not sources:
                raise ValueError("planner response had no grounded sources")
            options: list[TravelPlanOption] = []
            for index, item in enumerate(data[:3], start=1):
                if not isinstance(item, dict):
                    continue
                item["option_id"] = str(item.get("option_id") or f"live-{index}")[:40]
                item["generated_at"] = now
                item["availability"] = "LIVE"
                model_sources = item.get("source_links", [])
                if not isinstance(model_sources, list):
                    model_sources = []
                item["source_links"] = [*model_sources, *sources]
                options.append(TravelPlanOption.model_validate(item))
            if len(options) == 3:
                return options
        except Exception:
            # Do not expose SDK text (it can contain request URLs or credentials),
            # but leave an auditable bounded marker for Cloud Logging. The Telegram
            # view labels these options as ESTIMATE, so a provider outage can never
            # be mistaken for live availability.
            logger.warning(
                "vertex planning failed; returning deterministic estimate",
                extra={
                    "provider": "vertex-planner",
                    "error_code": "VERTEX_PLANNER_FALLBACK",
                    "result_class": "ESTIMATE",
                },
            )
        return await self._fallback.generate(
            request=request, now=now, telegram_user_id=telegram_user_id
        )

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
                telegram_user_id, telegram_chat_id, callback_data.removeprefix("plan:select:")
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
        if request is None:
            previous_context = draft.planning_context or self._context_from_request(
                draft.planning_request
            )
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
        self, telegram_user_id: str, telegram_chat_id: str, option_id: str
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
        option = next(item for item in saved.planning_options if item.option_id == option_id)
        return TelegramView(
            text=(
                f"<b>{option.title}</b> selected.\n\n{option.summary}\n\n"
                "This is a planning estimate, not a booking. Forward the real ticket, "
                "booking email, screenshot or .pkpass and I will connect it to this plan "
                "and start autonomous monitoring."
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(
                        text="Forward the real booking", callback_data="trip:forward_help"
                    )
                ],
                [TelegramButton(text="Save this plan", callback_data="plan:save")],
            ],
        )

    async def _save_plan(
        self, telegram_user_id: str, telegram_chat_id: str, *, now: datetime
    ) -> TelegramView:
        draft = await self._repository.get_trip_draft(telegram_user_id)
        if (
            draft is None
            or draft.telegram_chat_id != telegram_chat_id
            or not draft.selected_plan_id
        ):
            raise TelegramPlanningError("choose a plan option first")
        option = next(
            item for item in draft.planning_options if item.option_id == draft.selected_plan_id
        )
        saved = await self._repository.save_trip_draft(
            draft=draft.model_copy(update={"planning_saved_at": now, "updated_at": now}),
            expected_version=draft.version,
        )
        if saved is None:
            raise TelegramPlanningError("your plan changed; please choose it again")
        return TelegramView(
            text=(
                f"Plan saved: {option.title}.\n\n"
                "I will not pretend an estimate is a confirmed reservation. Forward the "
                "actual booking when you have it; then I will build the protected itinerary "
                "and watchpoints."
            ),
            buttons=[TelegramButton(text="Forward booking", callback_data="trip:forward_help")],
        )

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
                "I will return three Search-grounded estimates, even with flexible dates. They "
                "are not bookings; only a "
                "real forwarded reservation can activate monitoring."
            ),
            button_rows=[
                [TelegramButton(text="Enable personal ideas", callback_data="plan:preferences")],
                [TelegramButton(text="Add a booked itinerary", callback_data="trip:menu")],
            ],
        )

    @staticmethod
    def _options_view(draft: TripDraft) -> TelegramView:
        request = draft.planning_request
        if request is None:
            raise TelegramPlanningError("planning request is missing")
        text = [
            f"<b>Planning {request.destination}</b>",
            (
                f"flexible dates · {request.nights} nights · budget €{request.budget_eur}"
                if isinstance(request, FlexibleTravelPlanRequest)
                else f"{request.start_date} → {request.end_date} · budget €{request.budget_eur}"
            ),
            "",
            "Choose a route. These are planning estimates, not bookings.",
        ]
        if not any(option.availability == "LIVE" for option in draft.planning_options):
            text.insert(
                3,
                (
                    "Live Google Search is temporarily unavailable. These bounded offline "
                    "estimates are not current availability; try again later for refreshed sources."
                ),
            )
        rows: list[list[TelegramButton]] = []
        for option in draft.planning_options:
            source_label = "Search-grounded" if option.availability == "LIVE" else "estimate"
            text.append(
                f"\n<b>{option.title}</b> · €{option.estimated_total_eur} · {source_label}\n"
                f"{option.summary}\n{option.resilience_note}"
            )
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
        is_follow_up = bool(
            re.search(r"^(?:из|from)\b", normalized) and re.search(r"\d{4}-\d{2}-\d{2}", normalized)
        )
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
        if context.destination is None or context.budget_eur is None:
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
        return TelegramView(
            text=f"I have the start of your trip brief.\n\n{question}",
            buttons=[TelegramButton(text="Add a booked itinerary", callback_data="trip:menu")],
        )

    @staticmethod
    def _extract_destination(text: str) -> str | None:
        match = re.search(
            r"(?:хочу(?:\s+поехать)?|поездк\w*|trip|travel)?\s*(?:в|in|to)\s+(.+?)"
            r"(?=\s+(?:на|for)\s+\d+\s*(?:ноч|night|дн|day)|\s+(?:за|under|до|budget|бюджет)\b|"
            r"\s+(?:из|from)\b|,|$)",
            text,
            re.IGNORECASE,
        )
        return " ".join(match.group(1).split()).strip(" ,") if match else None

    @staticmethod
    def _extract_origin(text: str) -> str | None:
        match = re.search(
            r"(?:из|from)\s+(.+?)(?=\s+(?:на|for)\s+\d|\s+\d{4}-\d{2}-\d{2}|,|$)",
            text,
            re.IGNORECASE,
        )
        return " ".join(match.group(1).split()).strip(" ,") if match else None

    @staticmethod
    def _extract_nights(text: str) -> int | None:
        match = re.search(r"\b(\d+)\s*(ноч(?:ей|и|ь)?|nights?)\b", text, re.IGNORECASE)
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
